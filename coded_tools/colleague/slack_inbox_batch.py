"""Host-owned Slack inbox batches and delivery acknowledgements."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from coded_tools.colleague._runtime import atomic_write_json
from coded_tools.colleague._runtime import exclusive_file_lock
from coded_tools.colleague._runtime import has_active_lease
from coded_tools.colleague._runtime import read_json
from coded_tools.colleague.slack_event_queue import EVENT_ID_RE

SLACK_TS_RE = re.compile(r"(?:0|\d+\.\d+)")
BATCH_ID_RE = re.compile(r"[0-9a-f-]{36}")
MAX_BATCHES = 100


def create_batch(run_id: str, checkpoint_ts: str, messages: list[dict[str, Any]]) -> str:
    """Persist body-free request metadata and return an opaque batch ID."""
    if not has_active_lease(run_id):
        raise ValueError("run_id does not own an active colleague lease")
    if not SLACK_TS_RE.fullmatch(checkpoint_ts):
        raise ValueError("checkpoint_ts must be a Slack timestamp")
    requests: dict[str, dict[str, Any]] = {}
    for message in messages:
        timestamp = str(message.get("ts", ""))
        thread_ts = str(message.get("thread_ts") or timestamp)
        event_ids = message.get("event_ids", [])
        if not SLACK_TS_RE.fullmatch(timestamp) or not SLACK_TS_RE.fullmatch(thread_ts):
            raise ValueError("Slack request metadata has an invalid timestamp")
        if not isinstance(event_ids, list) or any(
            not isinstance(event_id, str) or not EVENT_ID_RE.fullmatch(event_id) for event_id in event_ids
        ):
            raise ValueError("Slack request metadata has an invalid event ID")
        requests[timestamp] = {
            "thread_ts": thread_ts,
            "event_ids": sorted(set(event_ids)),
            "delivered": False,
        }

    path = _path()
    with exclusive_file_lock(path):
        state = _load(path)
        batches = _recent_batches(state["batches"])
        if len(batches) >= MAX_BATCHES:
            raise ValueError("Slack inbox batch state is full")
        batch_id = str(uuid4())
        batches[batch_id] = {
            "run_id": run_id,
            "checkpoint_ts": checkpoint_ts,
            "created_at": time.time(),
            "requests": requests,
        }
        atomic_write_json(path, {"batches": batches})
    return batch_id


def reply_thread(batch_id: str, run_id: str, request_ts: str) -> str:
    """Resolve a model-supplied request handle to host-owned thread metadata."""
    batch, request = _get_request(batch_id, run_id, request_ts)
    del batch
    return str(request["thread_ts"])


def mark_delivered(batch_id: str, run_id: str, request_ts: str) -> None:
    """Record that Slack accepted (or previously accepted) the request reply."""
    if not has_active_lease(run_id):
        raise ValueError("run_id does not own an active colleague lease")
    path = _path()
    with exclusive_file_lock(path):
        state = _load(path)
        batch = _validated_batch(state, batch_id, run_id)
        request = batch["requests"].get(request_ts)
        if not isinstance(request, dict):
            raise ValueError("reply_to_ts is not part of this inbox batch")
        request["delivered"] = True
        atomic_write_json(path, state)


def validate_ready_batch(batch_id: str, run_id: str, checkpoint_ts: str) -> list[str]:
    """Validate exact checkpoint ownership and return host-derived Socket event IDs."""
    path = _path()
    with exclusive_file_lock(path):
        state = _load(path)
        batch = _validated_batch(state, batch_id, run_id)
        return _ready_event_ids(batch, checkpoint_ts)


def consume_ready_batch(batch_id: str, run_id: str, checkpoint_ts: str) -> list[str]:
    """Atomically consume a fully delivered batch and return its Socket event IDs."""
    path = _path()
    with exclusive_file_lock(path):
        state = _load(path)
        batch = _validated_batch(state, batch_id, run_id)
        event_ids = _ready_event_ids(batch, checkpoint_ts)
        state["batches"].pop(batch_id, None)
        atomic_write_json(path, state)
    return event_ids


def _get_request(batch_id: str, run_id: str, request_ts: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not has_active_lease(run_id):
        raise ValueError("run_id does not own an active colleague lease")
    if not SLACK_TS_RE.fullmatch(request_ts):
        raise ValueError("reply_to_ts must be a Slack timestamp")
    path = _path()
    with exclusive_file_lock(path):
        state = _load(path)
        batch = _validated_batch(state, batch_id, run_id)
        request = batch["requests"].get(request_ts)
        if not isinstance(request, dict):
            raise ValueError("reply_to_ts is not part of this inbox batch")
        return batch, request


def _ready_event_ids(batch: dict[str, Any], checkpoint_ts: str) -> list[str]:
    if batch.get("checkpoint_ts") != checkpoint_ts:
        raise ValueError("inbox batch does not match last_slack_ts")
    requests = batch.get("requests")
    if not isinstance(requests, dict):
        raise ValueError("Slack inbox batch state is invalid")
    undelivered = [timestamp for timestamp, value in requests.items() if not value.get("delivered")]
    if undelivered:
        raise ValueError("every Slack request must be delivered before advancing its checkpoint")
    event_ids = {
        event_id
        for value in requests.values()
        for event_id in value.get("event_ids", [])
        if isinstance(event_id, str) and EVENT_ID_RE.fullmatch(event_id)
    }
    return sorted(event_ids)


def _validated_batch(state: dict[str, Any], batch_id: str, run_id: str) -> dict[str, Any]:
    if not BATCH_ID_RE.fullmatch(batch_id):
        raise ValueError("inbox_batch_id is invalid")
    batch = state["batches"].get(batch_id)
    if not isinstance(batch, dict) or batch.get("run_id") != run_id:
        raise ValueError("inbox batch does not belong to this run")
    if not isinstance(batch.get("requests"), dict):
        raise ValueError("Slack inbox batch state is invalid")
    return batch


def _path() -> Path:
    state_path = Path(os.getenv("COLLEAGUE_STATE_PATH", ".state/colleague.json"))
    return state_path.with_name("slack_inbox_batches.json")


def _load(path: Path) -> dict[str, dict[str, Any]]:
    state = read_json(path, {"batches": {}})
    batches = state.get("batches")
    if not isinstance(batches, dict):
        raise ValueError("Slack inbox batch state is invalid")
    state["batches"] = _recent_batches(batches)
    return state


def _recent_batches(batches: dict[str, Any]) -> dict[str, dict[str, Any]]:
    try:
        ttl = max(900, int(os.getenv("COLLEAGUE_MAX_RUN_SECONDS", "600")) + 300)
    except ValueError:
        ttl = 900
    now = time.time()
    return {
        key: value
        for key, value in batches.items()
        if BATCH_ID_RE.fullmatch(str(key))
        and isinstance(value, dict)
        and isinstance(value.get("created_at"), (int, float))
        and now - float(value["created_at"]) <= ttl
    }
