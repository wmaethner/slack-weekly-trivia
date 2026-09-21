import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from slack_sdk.errors import SlackApiError

from slack_blocks import build_weekly_leaderboard_blocks, build_monthly_recap_blocks

ET = ZoneInfo("America/New_York")


class DailyTriviaScheduler:
    """Schedules and dispatches cron-triggered Slack posts: daily trivia,
    weekly leaderboard, monthly recap. Message content lives in
    slack_blocks.py — this class only decides when to run and posts
    whatever it builds.
    """

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

    # ------------------------------------------------------------------
    # Job registration
    # ------------------------------------------------------------------

    def _sync_jobs(self):
        configs = self._service.get_all_configs()
        if not configs:
            return

        self._register_jobs(
            configs, "trivia_", self._post_trivia, self._daily_trigger,
            misfire_grace_time=300,  # 5 min grace if app was down
            log_label="trivia",
        )

        leaderboard_trigger = CronTrigger(
            day_of_week=4, hour=15, minute=0, timezone="America/New_York"
        )
        self._register_jobs(
            configs, "leaderboard_", self._post_leaderboard,
            lambda cfg: leaderboard_trigger,
            misfire_grace_time=900,  # 15 min grace
            log_label="leaderboard (Fri 15:00 ET)",
        )

        monthly_trigger = CronTrigger(
            day=1, hour=12, minute=0, timezone="America/New_York"
        )
        self._register_jobs(
            configs, "monthly_extremes_", self._post_monthly_extremes,
            lambda cfg: monthly_trigger,
            misfire_grace_time=3600,  # 1 hour grace
            log_label="monthly extremes (1st 12:00 ET)",
        )

        self._prune_stale_jobs(
            configs, ("trivia_", "leaderboard_", "monthly_extremes_")
        )

    def _register_jobs(self, configs, id_prefix, func, trigger_factory,
                        misfire_grace_time, log_label):
        for cfg in configs:
            channel_id = cfg["channel_id"]
            team_id = cfg["team_id"]
            if not channel_id:
                logging.warning(
                    "Scheduler: skipping team %s — channel not configured", team_id
                )
                continue

            trigger = trigger_factory(cfg)
            if trigger is None:
                continue

            self._scheduler.add_job(
                func,
                trigger,
                args=[channel_id, team_id],
                id=f"{id_prefix}{team_id}",
                replace_existing=True,
                misfire_grace_time=misfire_grace_time,
            )
            logging.info(
                "Scheduler: %s job team=%s channel=%s", log_label, team_id, channel_id
            )

    def _prune_stale_jobs(self, configs, prefixes):
        """Remove jobs for teams that no longer have configs."""
        active_ids = {f"{p}{c['team_id']}" for p in prefixes for c in configs}
        for job in list(self._scheduler.get_jobs()):
            if any(job.id.startswith(p) for p in prefixes) and job.id not in active_ids:
                job.remove()
                logging.info("Scheduler: removed job %s", job.id)

    def _daily_trigger(self, cfg):
        try:
            h, m = cfg["post_time"].split(":")
            hour, minute = int(h), int(m)
        except (ValueError, AttributeError):
            logging.warning(
                "Scheduler: invalid post_time=%s for team %s",
                cfg["post_time"], cfg["team_id"],
            )
            return None
        return CronTrigger(
            hour=hour, minute=minute, day_of_week="0-4",  # Mon-Fri
            timezone="America/New_York",
        )

    # ------------------------------------------------------------------
    # Dispatch — fetch data, build blocks, post
    # ------------------------------------------------------------------

    def _post_trivia(self, channel_id, team_id):
        def build():
            public_blocks, _ = self._service.create_posted_question(channel_id)
            return "Daily Trivia", public_blocks

        self._safe_post(channel_id, team_id, "daily trivia", build)

    def _post_leaderboard(self, channel_id, team_id):
        def build():
            board = self._service.get_leaderboard(limit=5)
            category_tops = self._top_per_category(self._service.get_active_categories())
            start, end = self._week_window()
            questions = self._service.get_question_extremes(start, end)
            blocks = build_weekly_leaderboard_blocks(board, category_tops, questions)
            return "Weekly Leaderboard", blocks

        self._safe_post(channel_id, team_id, "weekly leaderboard", build)

    def _post_monthly_extremes(self, channel_id, team_id):
        def build():
            start, end = self._prev_month_window()
            questions = self._service.get_question_extremes(start, end)
            month_label = datetime.fromisoformat(start).astimezone(ET).strftime("%B %Y")
            blocks = build_monthly_recap_blocks(questions, month_label)
            return f"Monthly Trivia Recap — {month_label}", blocks

        self._safe_post(channel_id, team_id, "monthly extremes", build)

    def _top_per_category(self, categories, limit=5):
        tops = []
        for cat in categories[:limit]:
            board = self._service.get_leaderboard(limit=1, category=cat)
            if board:
                tops.append((cat, board[0]))
        return tops

    def _safe_post(self, channel_id, team_id, action, build_fn):
        try:
            text, blocks = build_fn()
            self._client.chat_postMessage(channel=channel_id, text=text, blocks=blocks)
            logging.info(
                "Scheduler: posted %s to %s (team=%s)", action, channel_id, team_id
            )
        except SlackApiError as e:
            if e.response.get("error") == "not_in_channel":
                logging.warning(
                    "Scheduler: bot not in channel %s — add with /invite", channel_id
                )
            else:
                logging.exception(
                    "Scheduler: failed to post %s to channel %s", action, channel_id
                )
        except Exception:
            logging.exception(
                "Scheduler: failed to post %s to channel %s", action, channel_id
            )

    # ------------------------------------------------------------------
    # Date windows
    # ------------------------------------------------------------------

    def _week_window(self):
        """UTC ISO bounds [start, end) for the trailing 7 days, ET wall clock."""
        now_et = datetime.now(ET)
        start_et = now_et - timedelta(days=7)
        return (
            start_et.astimezone(timezone.utc).isoformat(),
            now_et.astimezone(timezone.utc).isoformat(),
        )

    def _prev_month_window(self):
        """UTC ISO bounds [start, end) for the previous calendar month, ET wall clock."""
        now_et = datetime.now(ET)
        first_of_this_month = now_et.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        if first_of_this_month.month == 1:
            start_et = first_of_this_month.replace(
                year=first_of_this_month.year - 1, month=12
            )
        else:
            start_et = first_of_this_month.replace(
                month=first_of_this_month.month - 1
            )
        return (
            start_et.astimezone(timezone.utc).isoformat(),
            first_of_this_month.astimezone(timezone.utc).isoformat(),
        )
