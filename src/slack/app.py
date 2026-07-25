import os
import time

from dotenv import load_dotenv
from slack_bolt import App, Assistant
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.agent.graph import run as run_agent

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])
assistant = Assistant()

BOT_NAME = "Compass"

STREAM_CHUNK_WORDS = 12
STREAM_THROTTLE_SECONDS = 0.3

SUGGESTED_PROMPTS = [
    {"title": "How do I create a payment?", "message": "How do I create a payment?"},
    {
        "title": "What breaks if fraud changes?",
        "message": "What breaks if I change the fraud pre-auth contract?",
    },
    {"title": "Is transfers ready?", "message": "Is transfers safe to build against yet?"},
]


@assistant.thread_started
def handle_thread_started(say, set_title, set_suggested_prompts, logger):
    try:
        set_title(BOT_NAME)
    except Exception as e:
        logger.error(f"failed to set thread title: {e}")
    try:
        set_suggested_prompts(title=f"Ask {BOT_NAME} about our services", prompts=SUGGESTED_PROMPTS)
    except Exception as e:
        logger.error(f"failed to set suggested prompts: {e}")
    say(
        f"Hi, I'm {BOT_NAME}. Ask me anything about our microservices, their APIs, "
        "dependencies, or migration status."
    )


def _feedback_blocks(question: str) -> list[dict]:
    return [
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "👍"},
                    "action_id": "feedback_thumbsup",
                    "value": question[:200],
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "👎"},
                    "action_id": "feedback_thumbsdown",
                    "value": question[:200],
                },
            ],
        }
    ]


def _stream_answer(say_stream, say, question: str, answer: str, logger) -> None:
    try:
        stream = say_stream()
        words = answer.split(" ")
        for i in range(0, len(words), STREAM_CHUNK_WORDS):
            chunk = " ".join(words[i : i + STREAM_CHUNK_WORDS])
            stream.append(markdown_text=(" " if i else "") + chunk)
            time.sleep(STREAM_THROTTLE_SECONDS)
        stream.stop(blocks=_feedback_blocks(question))
    except Exception as e:
        logger.error(f"streaming failed, falling back to a single message: {e}")
        say(markdown_text=answer[:2900])
        say(text="Was this helpful?", blocks=_feedback_blocks(question))


@assistant.user_message
def handle_user_message(payload, say, say_stream, set_status, logger):
    question = payload.get("text", "")

    try:
        set_status("is thinking...")
    except Exception as e:
        logger.error(f"failed to set status: {e}")

    try:
        answer = run_agent(question, session_id=payload.get("channel"), user_id=payload.get("user"))
    except Exception as e:
        logger.error(f"agent run failed: {e}")
        say(text="Sorry, something went wrong answering that.")
        return

    _stream_answer(say_stream, say, question, answer, logger)


app.assistant(assistant)


@app.event("message")
def handle_plain_dm_message(event, say, logger):
    """Fallback for workspaces where assistant_thread_started never fires
    (seen on some free/sandbox workspaces), so the bot still answers DMs."""
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id") or event.get("subtype"):
        return

    question = event.get("text", "")

    try:
        say(text="Compass is looking into that...")
    except Exception as e:
        logger.error(f"failed to post thinking message: {e}")

    try:
        answer = run_agent(question, session_id=event.get("channel"), user_id=event.get("user"))
    except Exception as e:
        logger.error(f"agent run failed: {e}")
        say(text="Sorry, something went wrong answering that.")
        return

    say(markdown_text=answer[:2900])
    say(text="Was this helpful?", blocks=_feedback_blocks(question))


@app.action("feedback_thumbsup")
def handle_thumbsup(ack, body, logger):
    ack()
    print(f"[feedback] up from user {body['user']['id']} on message {body['message']['ts']}")


@app.action("feedback_thumbsdown")
def handle_thumbsdown(ack, body, logger):
    ack()
    print(f"[feedback] down from user {body['user']['id']} on message {body['message']['ts']}")


def main() -> None:
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()


if __name__ == "__main__":
    main()
