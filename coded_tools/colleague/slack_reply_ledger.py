"""Durable, body-free record of Slack requests that already received a reply."""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any

from coded_tools.colleague._runtime import atomic_write_json
from coded_tools.colleague._runtime import exclusive_file_lock
from coded_tools.colleague._runtime import read_json

SLACK_ID_RE = re.compile(r"[A-Z0-9]{2,40}")
SLACK_TS_RE = re.compile(r"\d+\.\d+")
RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_ENTRIES = 10_000


def answered_request_timestamps(channel: str, request_timestamps: list[str]) -> set[str]:
    """Return request timestamps that have a durable accepted-reply record."""
    _validate_channel(channel)
    for request_ts in request_timestamps:
        _validate_timestamp(request_ts)
    path = _path()
    with exclusive_file_lock(path):
        state = _load(path)
        return {
            request_ts
            for request_ts in request_timestamps
            if _request_key(channel, request_ts) in state["answered"]
        }


def request_was_answered(channel: str, request_ts: str) -> bool:
    """Return whether Slack already accepted a reply for this exact request."""
    return request_ts in answered_request_timestamps(channel, [request_ts])


def mark_request_answered(channel: str, request_ts: str, message_ts: str = "") -> None:
    """Persist an accepted reply without storing teammate or agent message bodies."""
    _validate_channel(channel)
    _validate_timestamp(request_ts)
    if message_ts:
        _validate_timestamp(message_ts)
    path = _path()
    now = time.time()
    with exclusive_file_lock(path):
        state = _load(path)
        state["answered"][_request_key(channel, request_ts)] = {
            "answered_at": now,
            "message_ts": message_ts,
        }
        if len(state["answered"]) > MAX_ENTRIES:
            ordered = sorted(
                state["answered"].items(),
                key=lambda item: float(item[1]["answered_at"]),
                reverse=True,
            )
            state["answered"] = dict(ordered[:MAX_ENTRIES])
        atomic_write_json(path, state)


def _path() -> Path:
    state_path = Path(os.getenv("COLLEAGUE_STATE_PATH", ".state/colleague.json"))
    return state_path.with_name("slack_reply_ledger.json")


def _load(path: Path) -> dict[str, dict[str, Any]]:
    state = read_json(path, {"answered": {}})
    answered = state.get("answered")
    if not isinstance(answered, dict):
        raise ValueError("Slack reply ledger is invalid")
    cutoff = time.time() - RETENTION_SECONDS
    recent: dict[str, dict[str, Any]] = {}
    for key, value in answered.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        answered_at = value.get("answered_at")
        if not isinstance(answered_at, (int, float)) or float(answered_at) < cutoff:
            continue
        message_ts = value.get("message_ts", "")
        if not isinstance(message_ts, str) or (message_ts and not SLACK_TS_RE.fullmatch(message_ts)):
            continue
        recent[key] = {"answered_at": float(answered_at), "message_ts": message_ts}
    return {"answered": recent}


def _request_key(channel: str, request_ts: str) -> str:
    return hashlib.sha256(f"{channel}\n{request_ts}".encode()).hexdigest()


def _validate_channel(channel: str) -> None:
    if not SLACK_ID_RE.fullmatch(channel):
        raise ValueError("Slack reply ledger channel is invalid")


def _validate_timestamp(timestamp: str) -> None:
    if not SLACK_TS_RE.fullmatch(timestamp):
        raise ValueError("Slack reply ledger timestamp is invalid")
