"""Announce coarse Colleague availability to the fixed Slack channel."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from coded_tools.colleague._runtime import append_audit
from coded_tools.colleague._runtime import env_bool
from coded_tools.colleague._slack_client import SlackApiClient
from coded_tools.colleague._slack_client import SlackApiError

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {
    "online": "Colleague is online and available.",
    "offline": "Colleague is offline and will not respond until it is restarted.",
}


def set_availability(status: str) -> bool:
    """Post one lifecycle notice; return false on a best-effort failure."""
    if status not in STATUSES:
        print("ERROR: availability must be online or offline")
        return False
    if not env_bool("COLLEAGUE_SLACK_AVAILABILITY_ENABLED", False):
        print(f"Slack availability notice skipped ({status} requested; notices are disabled).")
        return True
    if not env_bool("COLLEAGUE_SLACK_WRITE_ENABLED", False):
        print(f"Slack availability unchanged ({status} requested; Slack writes are disabled).")
        return True

    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    channel = os.getenv("SLACK_CHANNEL_ID", "").strip()
    if not token or not channel:
        print("WARNING: Slack availability was not changed because Slack is not configured.")
        return False

    try:
        prefix = os.getenv("COLLEAGUE_SLACK_MESSAGE_PREFIX", "[neuro-san colleague]").strip()
        text = f"{prefix} {STATUSES[status]}" if prefix else STATUSES[status]
        SlackApiClient(token).call(
            "chat.postMessage",
            http_method="POST",
            payload={
                "channel": channel,
                "text": text,
                "mrkdwn": False,
                "unfurl_links": False,
                "unfurl_media": False,
            },
        )
    except (OSError, TypeError, ValueError, SlackApiError) as exc:
        error = str(exc) if isinstance(exc, (ValueError, SlackApiError)) else "availability state unavailable"
        append_audit("slack_availability", ok=False, status=status, error=error)
        print(f"WARNING: Slack availability was not changed: {error}")
        return False

    append_audit("slack_availability", ok=True, status=status)
    print(f"Slack availability set to {status}.")
    return True
def main(argv: list[str] | None = None) -> int:
    """Load the project environment and perform a best-effort transition."""
    load_dotenv(ROOT / ".env")
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1 or arguments[0] not in STATUSES:
        print("Usage: python -m scripts.slack_availability {online|offline}")
        return 2
    set_availability(arguments[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
