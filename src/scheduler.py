import logging
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("scheduler")


class DailyTriviaScheduler:
    """Posts a daily trivia question to each configured channel at its set time."""

    def __init__(self, trivia_service, slack_client):
        self._service = trivia_service
        self._client = slack_client
        self._posted_hours = set()

    def start(self):
        thread = threading.Thread(target=self._run, daemon=True, name="scheduler")
        thread.start()
        logging.info("Scheduler started")

    def _run(self):
        # Run first tick immediately so user sees config status
        self._tick(initial=True)

        while True:
            time.sleep(60)
            try:
                self._tick()
            except Exception:
                logger.exception("Scheduler tick failed")

    def _tick(self, initial=False):
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%H:%M")
        configs = self._service.get_all_configs()

        if not configs and initial:
            logging.warning(
                "Scheduler: no workspaces configured. "
                "Use /set-trivia-channel and /set-trivia-time, then /post-trivia to test."
            )
            return

        # Reset posted_hours at midnight
        self._posted_hours = {
            k for k in self._posted_hours if k.startswith(now_str[:2])
        }

        for cfg in configs:
            team_id = cfg["team_id"]
            channel_id = cfg["channel_id"]
            post_time = cfg["post_time"]

            if initial:
                logging.info(
                    "Scheduler: team=%s channel=%s post_time=%s UTC",
                    team_id, channel_id, post_time,
                )

            if post_time != now_str:
                continue

            # Skip weekends (0=Mon ... 6=Sun)
            if now.weekday() >= 5:
                logging.info("Scheduler: skipping — weekend")
                return

            if not channel_id:
                logger.warning(
                    "Scheduler: skipping team %s — channel not configured", team_id
                )
                continue

            dedup_key = f"{now_str}:{team_id}"
            if dedup_key in self._posted_hours:
                continue

            try:
                public_blocks, _ = self._service.create_posted_question(channel_id)
                self._client.chat_postMessage(
                    channel=channel_id,
                    text="Daily Trivia",
                    blocks=public_blocks,
                )
                self._posted_hours.add(dedup_key)
                logging.info(
                    "Scheduler: posted daily trivia to %s (team=%s at %s UTC)",
                    channel_id, team_id, now_str,
                )
            except Exception:
                logger.exception(
                    "Scheduler: failed to post trivia to channel %s", channel_id
                )
