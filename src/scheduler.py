import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


class DailyTriviaScheduler:
    """Posts a daily trivia question to each configured channel at its set time."""

    def __init__(self, trivia_service, slack_client):
        self._service = trivia_service
        self._client = slack_client
        self._scheduler = BackgroundScheduler()

    def start(self):
        self._sync_jobs()
        # Re-sync every 5 minutes to pick up channel/time config changes
        self._scheduler.add_job(
            self._sync_jobs, "interval", minutes=5, id="sync"
        )
        self._scheduler.start()
        logging.info("Scheduler started (APScheduler)")

    def _sync_jobs(self):
        configs = self._service.get_all_configs()
        if not configs:
            return

        for cfg in configs:
            team_id = cfg["team_id"]
            channel_id = cfg["channel_id"]
            post_time = cfg["post_time"]

            if not channel_id:
                logging.warning(
                    "Scheduler: skipping team %s — channel not configured", team_id
                )
                continue

            try:
                h, m = post_time.split(":")
                hour, minute = int(h), int(m)
            except (ValueError, AttributeError):
                logging.warning(
                    "Scheduler: invalid post_time=%s for team %s", post_time, team_id
                )
                continue

            trigger = CronTrigger(
                hour=hour,
                minute=minute,
                day_of_week="0-4",  # Mon-Fri
                timezone="utc",
            )
            self._scheduler.add_job(
                self._post_trivia,
                trigger,
                args=[channel_id, team_id],
                id=f"trivia_{team_id}",
                replace_existing=True,
                misfire_grace_time=300,  # 5 min grace if app was down
            )
            logging.info(
                "Scheduler: team=%s channel=%s cron=%02d:%02d UTC (Mon-Fri)",
                team_id, channel_id, hour, minute,
            )

        # Remove jobs for teams that no longer have configs
        active_ids = {f"trivia_{c['team_id']}" for c in configs}
        for job in list(self._scheduler.get_jobs()):
            if job.id.startswith("trivia_") and job.id not in active_ids:
                job.remove()
                logging.info("Scheduler: removed job %s", job.id)

    def _post_trivia(self, channel_id, team_id):
        try:
            public_blocks, _ = self._service.create_posted_question(channel_id)
            self._client.chat_postMessage(
                channel=channel_id,
                text="Daily Trivia",
                blocks=public_blocks,
            )
            logging.info(
                "Scheduler: posted daily trivia to %s (team=%s)", channel_id, team_id
            )
        except Exception:
            logging.exception(
                "Scheduler: failed to post trivia to channel %s", channel_id
            )
