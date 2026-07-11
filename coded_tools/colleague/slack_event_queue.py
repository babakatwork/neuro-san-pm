"""Durable, body-free queue for Slack Socket Mode wake events."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from coded_tools.colleague._runtime import atomic_write_json
from coded_tools.colleague._runtime import exclusive_file_lock
from coded_tools.colleague._runtime import read_json

EVENT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,200}")
SLACK_ID_RE = re.compile(r"[A-Z0-9]{2,40}")
SLACK_TS_RE = re.compile(r"\d+\.\d+")
COMPLETED_TTL_SECONDS = 24 * 60 * 60
MAX_PENDING_EVENTS = 1000
MAX_DEAD_LETTERS = 1000


def queue_path() -> Path:
    """Return a queue path colocated with the main durable state."""
    state_path = Path(os.getenv("COLLEAGUE_STATE_PATH", ".state/colleague.json"))
    return state_path.with_name("slack_wake_events.json")


def claim_event(event_id: str, event: dict[str, Any]) -> bool:
    """Queue event metadata exactly once without persisting teammate text."""
    metadata = _metadata(event)
    if not EVENT_ID_RE.fullmatch(event_id) or metadata is None:
        return False
    path = queue_path()
    now = time.time()
    with exclusive_file_lock(path):
        state = _load(path)
        pending = state["pending"]
        completed = _recent_completed(state["completed"], now)
        if event_id in pending or event_id in completed or event_id in state["dead_letter"]:
            return False
        if len(pending) >= MAX_PENDING_EVENTS:
            raise ValueError("Slack wake queue is full")
        pending[event_id] = {**metadata, "received_at": now, "failure_count": 0}
        atomic_write_json(
            path,
            {"pending": pending, "completed": completed, "dead_letter": state["dead_letter"]},
        )
    return True


def release_event(event_id: str) -> None:
    """Release a pending reservation after HTTP dispatch failure."""
    if not EVENT_ID_RE.fullmatch(event_id):
        return
    path = queue_path()
    with exclusive_file_lock(path):
        state = _load(path)
        if event_id not in state["pending"]:
            return
        state["pending"].pop(event_id, None)
        atomic_write_json(path, state)


def pending_events(channel: str, allowed_users: set[str]) -> list[dict[str, str]]:
    """List validated pending references for the configured Slack boundary."""
    path = queue_path()
    with exclusive_file_lock(path):
        state = _load(path)
    events: list[dict[str, str]] = []
    for event_id, metadata in state["pending"].items():
        if not EVENT_ID_RE.fullmatch(str(event_id)) or not isinstance(metadata, dict):
            continue
        if metadata.get("channel") != channel or metadata.get("user") not in allowed_users:
            continue
        timestamp = str(metadata.get("ts", ""))
        thread_ts = str(metadata.get("thread_ts", ""))
        if not SLACK_TS_RE.fullmatch(timestamp) or not SLACK_TS_RE.fullmatch(thread_ts):
            continue
        events.append(
            {
                "event_id": str(event_id),
                "channel": channel,
                "user": str(metadata["user"]),
                "ts": timestamp,
                "thread_ts": thread_ts,
            }
        )
    events.sort(key=lambda item: float(item["ts"]))
    return events


def quarantine_ineligible_events(channel: str, allowed_users: set[str]) -> list[str]:
    """Quarantine queued events that no longer satisfy the host boundary."""
    path = queue_path()
    quarantined_ids: list[str] = []
    with exclusive_file_lock(path):
        state = _load(path)
        for event_id, metadata in list(state["pending"].items()):
            valid_metadata = isinstance(metadata, dict) and _stored_metadata_is_valid(metadata)
            boundary_matches = bool(
                valid_metadata
                and metadata.get("channel") == channel
                and metadata.get("user") in allowed_users
            )
            if boundary_matches:
                continue
            value = dict(metadata) if isinstance(metadata, dict) else {}
            value.update(
                {
                    "reason": "boundary_changed" if valid_metadata else "invalid_metadata",
                    "quarantined_at": time.time(),
                }
            )
            state["pending"].pop(event_id, None)
            state["dead_letter"][str(event_id)] = value
            quarantined_ids.append(str(event_id))
        if quarantined_ids:
            ordered = sorted(
                state["dead_letter"].items(),
                key=lambda item: float(item[1].get("quarantined_at", 0)),
                reverse=True,
            )
            state["dead_letter"] = dict(ordered[:MAX_DEAD_LETTERS])
            atomic_write_json(path, state)
    return quarantined_ids


def complete_events(event_ids: list[str]) -> None:
    """Move processed events to short-lived tombstones for retry deduplication."""
    unique_ids = {event_id for event_id in event_ids if EVENT_ID_RE.fullmatch(event_id)}
    if not unique_ids:
        return
    path = queue_path()
    now = time.time()
    with exclusive_file_lock(path):
        state = _load(path)
        completed = _recent_completed(state["completed"], now)
        for event_id in unique_ids:
            if event_id in state["pending"]:
                state["pending"].pop(event_id, None)
                completed[event_id] = now
        atomic_write_json(
            path,
            {"pending": state["pending"], "completed": completed, "dead_letter": state["dead_letter"]},
        )


def record_resolution_failure(event_id: str, reason: str) -> bool:
    """Increment a safe retry counter; return true when the event is quarantined."""
    if not EVENT_ID_RE.fullmatch(event_id):
        return True
    try:
        max_attempts = int(os.getenv("COLLEAGUE_SLACK_EVENT_MAX_ATTEMPTS", "3"))
    except ValueError:
        max_attempts = 3
    if not 1 <= max_attempts <= 10:
        max_attempts = 3
    path = queue_path()
    with exclusive_file_lock(path):
        state = _load(path)
        metadata = state["pending"].get(event_id)
        if not isinstance(metadata, dict):
            return True
        failures = int(metadata.get("failure_count", 0)) + 1
        metadata["failure_count"] = failures
        if failures < max_attempts:
            atomic_write_json(path, state)
            return False
        quarantined = {
            **metadata,
            "reason": re.sub(r"[^a-z0-9_-]", "_", reason.lower())[:80],
            "quarantined_at": time.time(),
        }
        state["pending"].pop(event_id, None)
        state["dead_letter"][event_id] = quarantined
        if len(state["dead_letter"]) > MAX_DEAD_LETTERS:
            ordered = sorted(
                state["dead_letter"].items(),
                key=lambda item: float(item[1].get("quarantined_at", 0)),
                reverse=True,
            )
            state["dead_letter"] = dict(ordered[:MAX_DEAD_LETTERS])
        atomic_write_json(path, state)
    return True


def dead_letters() -> dict[str, dict[str, Any]]:
    """Return body-free quarantined event metadata for operator inspection."""
    path = queue_path()
    with exclusive_file_lock(path):
        state = _load(path)
    return {key: dict(value) for key, value in state["dead_letter"].items() if isinstance(value, dict)}


def requeue_dead_letter(event_id: str) -> bool:
    """Move one quarantined event back to pending with a reset retry count."""
    if not EVENT_ID_RE.fullmatch(event_id):
        return False
    path = queue_path()
    with exclusive_file_lock(path):
        state = _load(path)
        metadata = state["dead_letter"].pop(event_id, None)
        if not isinstance(metadata, dict) or len(state["pending"]) >= MAX_PENDING_EVENTS:
            return False
        metadata.pop("reason", None)
        metadata.pop("quarantined_at", None)
        metadata["failure_count"] = 0
        state["pending"][event_id] = metadata
        atomic_write_json(path, state)
    return True


def drop_dead_letter(event_id: str) -> bool:
    """Acknowledge and remove one quarantined event by explicit operator action."""
    if not EVENT_ID_RE.fullmatch(event_id):
        return False
    path = queue_path()
    now = time.time()
    with exclusive_file_lock(path):
        state = _load(path)
        if event_id not in state["dead_letter"]:
            return False
        state["dead_letter"].pop(event_id, None)
        state["completed"] = _recent_completed(state["completed"], now)
        state["completed"][event_id] = now
        atomic_write_json(path, state)
    return True


def _metadata(event: dict[str, Any]) -> dict[str, str] | None:
    channel = str(event.get("channel", ""))
    user = str(event.get("user", ""))
    timestamp = str(event.get("ts", ""))
    thread_ts = str(event.get("thread_ts") or timestamp)
    if not (
        SLACK_ID_RE.fullmatch(channel)
        and SLACK_ID_RE.fullmatch(user)
        and SLACK_TS_RE.fullmatch(timestamp)
        and SLACK_TS_RE.fullmatch(thread_ts)
    ):
        return None
    return {"channel": channel, "user": user, "ts": timestamp, "thread_ts": thread_ts}


def _stored_metadata_is_valid(metadata: dict[str, Any]) -> bool:
    return bool(
        SLACK_ID_RE.fullmatch(str(metadata.get("channel", "")))
        and SLACK_ID_RE.fullmatch(str(metadata.get("user", "")))
        and SLACK_TS_RE.fullmatch(str(metadata.get("ts", "")))
        and SLACK_TS_RE.fullmatch(str(metadata.get("thread_ts", "")))
    )


def _load(path: Path) -> dict[str, dict[str, Any]]:
    state = read_json(path, {"pending": {}, "completed": {}, "dead_letter": {}})
    pending = state.get("pending")
    completed = state.get("completed")
    dead_letter = state.get("dead_letter", {})
    if not isinstance(pending, dict) or not isinstance(completed, dict) or not isinstance(dead_letter, dict):
        raise ValueError("Slack wake state is invalid")
    return {"pending": pending, "completed": completed, "dead_letter": dead_letter}


def _recent_completed(completed: dict[str, Any], now: float) -> dict[str, float]:
    return {
        key: float(value)
        for key, value in completed.items()
        if EVENT_ID_RE.fullmatch(str(key))
        and isinstance(value, (int, float))
        and now - float(value) <= COMPLETED_TTL_SECONDS
    }
