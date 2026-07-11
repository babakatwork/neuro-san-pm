"""Manually wake the colleague through Neuro SAN's event invocation path."""

from __future__ import annotations

import argparse
import os

import requests
from dotenv import load_dotenv


def build_payload(text: str) -> dict:
    """Build an event request with an explicit minimal chat filter."""
    return {
        "user_message": {"type": "HUMAN", "text": text},
        "chat_filter": {"chat_filter_type": "MINIMAL"},
    }


def main() -> None:
    """Trigger the event network and print only its immediate acknowledgement."""
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "text",
        nargs="?",
        default="Manual product-management heartbeat: inspect the board and notify Slack only if useful.",
    )
    args = parser.parse_args()
    base_url = os.getenv("NEURO_SAN_BASE_URL", "http://localhost:8080").rstrip("/")
    endpoint = f"{base_url}/api/v1/product_colleague/streaming_chat"
    response = requests.post(endpoint, json=build_payload(args.text), timeout=15)
    response.raise_for_status()
    print("Event accepted; background work continues in the Neuro SAN server.")


if __name__ == "__main__":
    main()
