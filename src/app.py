import logging
import os
import re

import requests
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from trivia_api import TriviaApi
from trivia_service import TriviaService

logging.basicConfig(level=logging.INFO)
load_dotenv()

bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
app_token = os.environ.get("SLACK_APP_TOKEN", "")
signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")

if not all([bot_token, app_token, signing_secret]):
    logging.error("Missing environment variables. Check .env file.")
    raise SystemExit(1)

app = App(token=bot_token)
trivia_service = TriviaService(TriviaApi())

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


# ------------------------------------------------------------------
# Actions
# ------------------------------------------------------------------


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
    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()
