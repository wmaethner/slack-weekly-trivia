import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from slack_sdk.errors import SlackApiError

from trivia_service import CATEGORY_EMOJI


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
                timezone="America/New_York",
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
                "Scheduler: team=%s channel=%s cron=%02d:%02d ET (Mon-Fri)",
                team_id, channel_id, hour, minute,
            )

        # Weekly leaderboard: Friday at noon Eastern (America/New_York)
        leaderboard_trigger = CronTrigger(
            day_of_week=4,
            hour=12,
            minute=0,
            timezone="America/New_York",
        )
        for cfg in configs:
            channel_id = cfg["channel_id"]
            team_id = cfg["team_id"]
            if not channel_id:
                continue
            self._scheduler.add_job(
                self._post_leaderboard,
                leaderboard_trigger,
                args=[channel_id, team_id],
                id=f"leaderboard_{team_id}",
                replace_existing=True,
                misfire_grace_time=900,  # 15 min grace
            )
            logging.info(
                "Scheduler: leaderboard job team=%s channel=%s (Fri 12:00 ET)",
                team_id, channel_id,
            )

        # Remove jobs for teams that no longer have configs
        active_ids = {f"trivia_{c['team_id']}" for c in configs}
        active_ids |= {f"leaderboard_{c['team_id']}" for c in configs}
        for job in list(self._scheduler.get_jobs()):
            if (job.id.startswith("trivia_") or job.id.startswith("leaderboard_")) \
                    and job.id not in active_ids:
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
        except SlackApiError as e:
            if e.response.get("error") == "not_in_channel":
                logging.warning(
                    "Scheduler: bot not in channel %s — add with /invite", channel_id
                )
            else:
                logging.exception(
                    "Scheduler: failed to post trivia to channel %s", channel_id
                )
        except Exception:
            logging.exception(
                "Scheduler: failed to post trivia to channel %s", channel_id
            )

    def _post_leaderboard(self, channel_id, team_id):
        TROPHIES = [
            ":first_place_medal:",
            ":second_place_medal:",
            ":third_place_medal:",
        ]

        board = self._service.get_leaderboard(limit=3)
        categories = self._service.get_active_categories()

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":trophy:  Weekly Leaderboard",
                    "emoji": True,
                },
            },
        ]

        if not board:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "No stats this week! Play daily trivia to get on the board.",
                    },
                }
            )
        else:
            lines = []
            for i, entry in enumerate(board):
                trophy = TROPHIES[i] if i < len(TROPHIES) else f"{i + 1}."
                lines.append(
                    f"{trophy}  <@{entry['user_id']}>  "
                    f"{entry['accuracy']}%  ({entry['correct']}/{entry['total']})"
                )
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Overall Top 3*\n" + "\n".join(lines)},
                }
            )

        # Top per category
        if categories:
            cat_lines = []
            for cat in categories[:5]:  # max 5 categories
                cat_board = self._service.get_leaderboard(
                    limit=1, category=cat
                )
                if cat_board:
                    top = cat_board[0]
                    emoji = CATEGORY_EMOJI.get(cat, ":grey_question:")
                    display = cat.replace("_", " ").title()
                    cat_lines.append(
                        f"{emoji}  {display}: <@{top['user_id']}>"
                        f" {top['accuracy']}%"
                    )
            if cat_lines:
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*Top by Category*\n" + "\n".join(cat_lines),
                        },
                    }
                )

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Use `/leaderboard` for filters and `/stats` for your own results.",
                    }
                ],
            }
        )

        try:
            self._client.chat_postMessage(
                channel=channel_id,
                text="Weekly Leaderboard",
                blocks=blocks,
            )
            logging.info(
                "Scheduler: posted weekly leaderboard to %s (team=%s)",
                channel_id, team_id,
            )
        except SlackApiError as e:
            if e.response.get("error") == "not_in_channel":
                logging.warning(
                    "Scheduler: bot not in channel %s — add with /invite", channel_id
                )
            else:
                logging.exception(
                    "Scheduler: failed to post leaderboard to channel %s", channel_id
                )
        except Exception:
            logging.exception(
                "Scheduler: failed to post leaderboard to channel %s", channel_id
            )
