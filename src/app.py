import logging
import os
import re
import signal
import threading

import requests
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.errors import SlackApiError

from trivia_api import TriviaApi
from trivia_service import TriviaService
from stats_store import StatsStore
from scheduler import DailyTriviaScheduler
from slack_blocks import build_stats_blocks, build_leaderboard_command_blocks

logging.basicConfig(level=logging.INFO)
load_dotenv()

bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
app_token = os.environ.get("SLACK_APP_TOKEN", "")
signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")

if not all([bot_token, app_token, signing_secret]):
    logging.error("Missing environment variables. Check .env file.")
    raise SystemExit(1)

app = App(token=bot_token)
stats_store = StatsStore()
trivia_service = TriviaService(TriviaApi(), stats_store)

leaderboard_filters = {}  # user_id -> {"category": ..., "difficulty": ...}

# ------------------------------------------------------------------
# Events
# ------------------------------------------------------------------


@app.event("app_mention")
def handle_mentions(event, client, logger):
    channel = event["channel"]
    logger.info(f"Mention handler: channel={channel}")
    try:
        client.chat_postMessage(channel=channel, text="Ready for trivia!")
    except Exception as e:
        logger.error(f"Failed to respond to mention: {e}")


@app.event("message")
def handle_message(event, client, logger):
    if "subtype" in event:
        return

    channel = event.get("channel", "")
    user = event.get("user", "")
    text = event.get("text", "")
    logger.info(f"Message: channel={channel} user={user} text={text[:80]}")

    if channel.startswith("D"):
        try:
            client.chat_postMessage(channel=channel, text="Ready for trivia!")
        except Exception as e:
            logger.error(f"Failed to respond to DM: {e}")


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------


@app.command("/stats")
def handle_stats_command(ack, command, client, respond, logger):
    ack()

    channel = command["channel_id"]
    user = command["user_id"]
    logger.info(f"/stats from user={user}")

    try:
        stats = trivia_service.get_user_stats(user)
    except Exception as e:
        logger.error(f"Failed to fetch stats: {e}", exc_info=True)
        respond("Something went wrong fetching stats. Try again.")
        return

    if stats["total"] == 0:
        respond("No trivia stats yet. Run `/trivia` to play!")
        return

    blocks = build_stats_blocks(stats)

    client.chat_postEphemeral(
        channel=channel,
        user=user,
        text="Trivia Stats",
        blocks=blocks,
    )


@app.command("/leaderboard")
def handle_leaderboard_command(ack, command, client, respond, logger):
    ack()

    channel = command["channel_id"]
    user = command["user_id"]
    logger.info(f"/leaderboard from user={user}")

    leaderboard_filters[user] = {"category": None, "difficulty": None}
    blocks = _fetch_leaderboard_blocks(user, None, None)
    client.chat_postEphemeral(
        channel=channel,
        user=user,
        text="Leaderboard",
        blocks=blocks,
    )


@app.command("/post-trivia")
def handle_post_trivia_command(ack, command, client, respond, logger):
    ack()

    channel = command["channel_id"]
    user = command["user_id"]
    logger.info(f"/post-trivia from user={user} channel={channel}")

    try:
        public_blocks, _ = trivia_service.create_posted_question(channel)
        client.chat_postMessage(
            channel=channel,
            text="Daily Trivia",
            blocks=public_blocks,
        )
    except SlackApiError as e:
        if e.response.get("error") == "not_in_channel":
            respond("I'm not in this channel. Add me via `/invite @YourBotName` first.")
        else:
            logger.error(f"Failed to post trivia: {e}", exc_info=True)
            respond("Something went wrong. Try again.")
    except Exception as e:
        logger.error(f"Failed to post trivia: {e}", exc_info=True)
        respond("Something went wrong. Try again.")


@app.command("/set-trivia-channel")
def handle_set_channel_command(ack, command, client, logger):
    ack()

    channel = command["channel_id"]
    team = command["team_id"]
    user = command["user_id"]
    logger.info(f"/set-trivia-channel from user={user}")

    trivia_service.set_channel_config(team, channel)
    client.chat_postEphemeral(
        channel=channel,
        user=user,
        text=f"Daily trivia set to post in <#{channel}>. (Add me to this channel with `/invite @YourBotName`)",
    )


@app.command("/set-trivia-time")
def handle_set_time_command(ack, command, client, respond, logger):
    ack()

    channel = command["channel_id"]
    team = command["team_id"]
    user = command["user_id"]
    text = command.get("text", "").strip()
    logger.info(f"/set-trivia-time from user={user} text={text}")

    if not text:
        respond("Usage: `/set-trivia-time HH:MM` (ET, 24-hour). Example: `/set-trivia-time 12:00`")
        return

    try:
        # Validate format
        parts = text.split(":")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        respond("Invalid time format. Use HH:MM (ET, 24-hour). Example: `/set-trivia-time 12:00`")
        return

    post_time = f"{hour:02d}:{minute:02d}"
    trivia_service.set_post_time(team, post_time)
    client.chat_postEphemeral(
        channel=channel,
        user=user,
        text=f"Daily trivia post time set to {post_time} ET",
    )


# ------------------------------------------------------------------
# Actions
# ------------------------------------------------------------------


@app.action("leaderboard_category")
def handle_leaderboard_category(ack, body, logger):
    ack()
    _update_leaderboard(body, "category", body["actions"][0]["selected_option"]["value"])


@app.action("leaderboard_difficulty")
def handle_leaderboard_difficulty(ack, body, logger):
    ack()
    _update_leaderboard(body, "difficulty", body["actions"][0]["selected_option"]["value"])


def _update_leaderboard(body, key, value):
    user = body["user"]["id"]
    filters = leaderboard_filters.get(user, {"category": None, "difficulty": None})

    if value == "all":
        filters[key] = None
    else:
        filters[key] = value

    leaderboard_filters[user] = filters
    blocks = _fetch_leaderboard_blocks(user, filters["category"], filters["difficulty"])

    requests.post(
        body["response_url"],
        json={"replace_original": "true", "blocks": blocks},
        timeout=10,
    )


def _fetch_leaderboard_blocks(user, category, difficulty):
    board = trivia_service.get_leaderboard(limit=5, category=category, difficulty=difficulty)
    categories = trivia_service.get_active_categories()
    difficulties = trivia_service.get_active_difficulties()
    streaks = trivia_service.get_streak_leaderboard(limit=3)
    my_rank = trivia_service.get_user_rank(user, category, difficulty)
    answer_count = 0 if my_rank else trivia_service.get_user_answer_count(user)
    return build_leaderboard_command_blocks(
        board, categories, difficulties, category, difficulty,
        streaks, my_rank, answer_count,
    )


@app.action("start_answer")
def handle_start_answer(ack, body, client, logger):
    ack()

    user = body["user"]["id"]
    channel = body["channel"]["id"]
    question_id = body["actions"][0]["value"]
    logger.info(f"start_answer user={user} qid={question_id}")

    blocks = trivia_service.get_answer_blocks(question_id)
    if blocks is None:
        client.chat_postEphemeral(
            channel=channel,
            user=user,
            text="This question has expired. Wait for the next daily trivia!",
        )
        return

    client.chat_postEphemeral(
        channel=channel,
        user=user,
        text="Trivia Question",
        blocks=blocks,
    )


@app.action(re.compile(r"posted_trivia_answer_.*"))
def handle_posted_trivia_answer(ack, body, logger):
    ack()

    user = body["user"]["id"]
    raw_value = body["actions"][0]["value"]
    parts = raw_value.split("|", 1)
    selected_label = parts[0]
    question_id = parts[1] if len(parts) > 1 else None

    result_blocks = trivia_service.check_posted_answer(
        question_id, user, selected_label
    )
    if result_blocks is None:
        requests.post(
            body["response_url"],
            json={
                "replace_original": "true",
                "text": "You already answered this one! :white_check_mark:",
            },
            timeout=10,
        )
        return

    requests.post(
        body["response_url"],
        json={"replace_original": "true", "blocks": result_blocks},
        timeout=10,
    )
    logger.info(
        f"Posted answer: user={user} label={selected_label} qid={question_id}"
    )


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main():
    def shutdown(signum, frame):
        logging.info("Shutting down...")
        stats_store.close()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    from admin_server import start_in_thread as start_admin
    start_admin()

    scheduler = DailyTriviaScheduler(trivia_service, app.client)
    scheduler.start()

    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()
