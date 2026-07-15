"""Hardened Slack Socket Mode bridge that wakes the event-configured network."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests
from dotenv import load_dotenv

from coded_tools.colleague._runtime import read_env_bool
from coded_tools.colleague.slack_event_queue import claim_event
from coded_tools.colleague.slack_event_queue import release_event

LOGGER = logging.getLogger(__name__)


def allowed_users() -> set[str]:
    """Return the configured Slack user allowlist."""
    return {value.strip() for value in os.getenv("SLACK_ALLOWED_USER_IDS", "").split(",") if value.strip()}


def is_allowed_event(event: dict[str, Any]) -> bool:
    """Accept human messages only from the fixed channel and explicit user list."""
    base_allowed = bool(
        event.get("channel") == os.getenv("SLACK_CHANNEL_ID", "").strip()
        and event.get("user") in allowed_users()
        and not event.get("bot_id")
        and not event.get("subtype")
    )
    if not base_allowed:
        return False
    require_mention, mention_error = read_env_bool("COLLEAGUE_SLACK_REQUIRE_MENTION", True)
    if mention_error:
        return False
    if not require_mention:
        return True
    bot_user_id = os.getenv("SLACK_BOT_USER_ID", "").strip()
    return bool(bot_user_id and f"<@{bot_user_id}>" in str(event.get("text", "")))


def build_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Build a wake-only ChatRequest; Slack text is read by the durable inbox."""
    thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
    channel_id = str(event.get("channel", ""))
    user_id = str(event.get("user", ""))
    return {
        "user_message": {
            "type": "HUMAN",
            "text": "TRUSTED_SLACK_WAKE\nRead new requests through SlackInbox before doing any work.",
        },
        "sly_data": {
            "slack_channel_id": channel_id,
            "slack_thread_ts": thread_ts,
            "slack_user_id": user_id,
        },
        "chat_filter": {"chat_filter_type": "MINIMAL"},
    }


def dispatch_event(event: dict[str, Any]) -> None:
    """Send an allowlisted Slack event to the Neuro SAN HTTP service."""
    base_url = os.getenv("NEURO_SAN_BASE_URL", "http://localhost:8188").rstrip("/")
    network = os.getenv("COLLEAGUE_NETWORK_NAME", "product_colleague").strip()
    endpoint = f"{base_url}/api/v1/{network}/streaming_chat"
    response = requests.post(endpoint, json=build_event_payload(event), timeout=15)
    response.raise_for_status()


def create_app() -> Any:
    """Create the Slack Bolt app; imports stay lazy for offline validation/tests."""
    # pylint: disable=import-outside-toplevel,import-error
    from slack_bolt import App

    bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("SLACK_BOT_TOKEN is required")
    if not os.getenv("SLACK_CHANNEL_ID", "").strip() or not allowed_users():
        raise ValueError("SLACK_CHANNEL_ID and SLACK_ALLOWED_USER_IDS are required")
    require_mention, mention_error = read_env_bool("COLLEAGUE_SLACK_REQUIRE_MENTION", True)
    if mention_error:
        raise ValueError(mention_error)
    if require_mention and not os.getenv("SLACK_BOT_USER_ID", "").strip():
        raise ValueError("SLACK_BOT_USER_ID is required when mention filtering is enabled")
    app = App(token=bot_token)

    def handle(event: dict[str, Any], body: dict[str, Any], logger: Any) -> None:
        if not is_allowed_event(event):
            return
        event_id = str(body.get("event_id", ""))
        try:
            if not claim_event(event_id, event):
                return
            dispatch_event(event)
        except (OSError, ValueError, requests.RequestException):
            try:
                release_event(event_id)
            except (OSError, ValueError):
                logger.exception("Failed to release Slack event reservation")
            logger.exception("Failed to dispatch allowlisted Slack event")

    app.event("app_mention")(handle)

    def handle_direct_message(event: dict[str, Any], body: dict[str, Any], logger: Any) -> None:
        if event.get("channel_type") in {"im", "mpim"}:
            handle(event, body, logger)

    app.event("message")(handle_direct_message)
    return app


def main() -> None:
    """Start a Socket Mode listener."""
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app_token = os.getenv("SLACK_APP_TOKEN", "").strip()
    if not app_token:
        raise ValueError("SLACK_APP_TOKEN is required")
    # pylint: disable=import-outside-toplevel,import-error
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    LOGGER.info("Starting Slack bridge for one allowlisted channel")
    SocketModeHandler(create_app(), app_token).start()


if __name__ == "__main__":
    main()
