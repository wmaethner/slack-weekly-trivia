import logging
import os
import re
import signal

import requests
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from trivia_api import TriviaApi
from trivia_service import CATEGORY_EMOJI, TriviaService
from stats_store import StatsStore

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


@app.command("/trivia")
def handle_trivia_command(ack, command, client, respond, logger):
    ack()

    channel = command["channel_id"]
    user = command["user_id"]
    logger.info(f"/trivia from user={user} channel={channel}")

    try:
        blocks = trivia_service.create_question(user)
        client.chat_postEphemeral(
            channel=channel,
            user=user,
            text="Trivia Question",
            blocks=blocks,
        )
        logger.info(f"Trivia question sent to user={user}")
    except Exception as e:
        logger.error(f"Failed: {e}", exc_info=True)
        respond("Something went wrong. Try again.")


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

    total = stats["total"]
    correct = stats["correct"]
    accuracy = stats["accuracy"]

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":bar_chart:  Your Trivia Stats",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Total:* {total}  |  *Correct:* {correct}  |"
                    f"  *Wrong:* {total - correct}  |  *Accuracy:* {accuracy}%"
                ),
            },
        },
    ]

    if stats["by_difficulty"]:
        lines = []
        for level in ["easy", "medium", "hard"]:
            d = stats["by_difficulty"].get(level)
            if d:
                lines.append(
                    f"• {level.title()}: {d['correct']}/{d['total']}"
                    f" ({d['accuracy']}%)"
                )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*By Difficulty*\n" + "\n".join(lines),
                },
            }
        )

    if stats["by_category"]:
        lines = []
        for cat, d in stats["by_category"].items():
            emoji = CATEGORY_EMOJI.get(cat, ":grey_question:")
            display = cat.replace("_", " ").title()
            lines.append(
                f"{emoji}  {display}: {d['correct']}/{d['total']}"
                f" ({d['accuracy']}%)"
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*By Category*\n" + "\n".join(lines),
                },
            }
        )

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
    blocks = _build_leaderboard_blocks(user, None, None)
    client.chat_postEphemeral(
        channel=channel,
        user=user,
        text="Leaderboard",
        blocks=blocks,
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
    blocks = _build_leaderboard_blocks(user, filters["category"], filters["difficulty"])

    requests.post(
        body["response_url"],
        json={"replace_original": "true", "blocks": blocks},
        timeout=10,
    )


def _find_option(options, value):
    for opt in options:
        if opt["value"] == value:
            return opt
    return options[0]


def _build_leaderboard_blocks(user, category, difficulty):
    TROPHIES = [":first_place_medal:", ":second_place_medal:", ":third_place_medal:"]

    board = trivia_service.get_leaderboard(
        limit=3, category=category, difficulty=difficulty
    )
    categories = trivia_service.get_active_categories()
    difficulties = trivia_service.get_active_difficulties()

    cat_options = [
        {
            "text": {"type": "plain_text", "text": "All Categories", "emoji": True},
            "value": "all",
        }
    ]
    for cat in categories:
        cat_options.append(
            {
                "text": {
                    "type": "plain_text",
                    "text": f"{CATEGORY_EMOJI.get(cat, ':grey_question:')}  {cat.replace('_', ' ').title()}",
                    "emoji": True,
                },
                "value": cat,
            }
        )

    diff_options = [
        {
            "text": {"type": "plain_text", "text": "All Difficulties", "emoji": True},
            "value": "all",
        }
    ]
    for d in difficulties:
        diff_options.append(
            {
                "text": {
                    "type": "plain_text",
                    "text": d.title(),
                    "emoji": True,
                },
                "value": d,
            }
        )

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":trophy:  Leaderboard",
                "emoji": True,
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Category",
                        "emoji": True,
                    },
                    "options": cat_options,
                    "action_id": "leaderboard_category",
                    **(
                        {"initial_option": _find_option(cat_options, category)}
                        if category
                        else {}
                    ),
                },
                {
                    "type": "static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Difficulty",
                        "emoji": True,
                    },
                    "options": diff_options,
                    "action_id": "leaderboard_difficulty",
                    **(
                        {"initial_option": _find_option(diff_options, difficulty)}
                        if difficulty
                        else {}
                    ),
                },
            ],
        },
    ]

    if not board:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "No stats yet! Play `/trivia` to get on the board.",
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
                "text": {"type": "mrkdwn", "text": "\n".join(lines)},
            }
        )

    active_filters = []
    if category:
        display = category.replace("_", " ").title()
        active_filters.append(f"{CATEGORY_EMOJI.get(category, '')} {display}")
    if difficulty:
        active_filters.append(difficulty.title())
    filter_text = "  •  ".join(active_filters) if active_filters else "All"

    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Filters: {filter_text}"}
            ],
        }
    )

    return blocks


@app.action(re.compile(r"trivia_answer_.*"))
def handle_trivia_answer(ack, body, logger):
    ack()

    user = body["user"]["id"]
    selected_label = body["actions"][0]["value"]

    result_blocks = trivia_service.check_answer(user, selected_label)
    if result_blocks is None:
        return

    requests.post(
        body["response_url"],
        json={"replace_original": "true", "blocks": result_blocks},
        timeout=10,
    )
    logger.info(f"Answer processed: user={user} label={selected_label}")


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

    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()
