"""Durable checkpoint and overlap-lease tool for scheduled colleague runs."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._runtime import append_audit
from coded_tools.colleague._runtime import atomic_write_json
from coded_tools.colleague._runtime import exclusive_file_lock
from coded_tools.colleague._runtime import json_result
from coded_tools.colleague._runtime import read_json
from coded_tools.colleague._runtime import utc_now_iso
from coded_tools.colleague.slack_event_queue import complete_events
from coded_tools.colleague.slack_inbox_batch import consume_ready_batch
from coded_tools.colleague.slack_inbox_batch import validate_ready_batch

DEFAULT_STATE: dict[str, Any] = {
    "version": 1,
    "board_snapshot": None,
    "last_slack_ts": "0",
    "last_report_at": None,
    "last_notified_digest": None,
    "run": None,
}

SHA256_RE = re.compile(r"[0-9a-f]{64}")
SLACK_TS_RE = re.compile(r"(?:0|\d+\.\d+)")


class ColleagueState(CodedTool):
    """Read/checkpoint state and provide a renewable single-run lease."""

    @staticmethod
    def _path() -> Path:
        return Path(os.getenv("COLLEAGUE_STATE_PATH", ".state/colleague.json"))

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del sly_data
        action = str(args.get("action", "read")).strip().lower()
        try:
            if action == "read":
                return self._read()
            if action == "begin":
                return self._begin()
            if action == "checkpoint":
                return self._checkpoint(args)
            if action == "finish":
                return self._finish(args)
            return json_result(ok=False, error="action must be read, begin, checkpoint, or finish")
        except (OSError, ValueError) as exc:
            append_audit("state_error", action=action, error_type=type(exc).__name__)
            return json_result(ok=False, error=str(exc))

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)

    def _load(self) -> dict[str, Any]:
        state = read_json(self._path(), DEFAULT_STATE)
        for key, value in DEFAULT_STATE.items():
            state.setdefault(key, value)
        return state

    def _read(self) -> str:
        with exclusive_file_lock(self._path()):
            state = self._load()
        return json_result(ok=True, state=state)

    def _begin(self) -> str:
        now = time.time()
        lease_seconds = max(60, int(os.getenv("COLLEAGUE_MAX_RUN_SECONDS", "600")) + 60)
        with exclusive_file_lock(self._path()):
            state = self._load()
            active_run = state.get("run")
            if isinstance(active_run, dict) and float(active_run.get("lease_until", 0)) > now:
                return json_result(
                    ok=True,
                    acquired=False,
                    reason="another run owns the lease",
                    lease_until=active_run.get("lease_until"),
                )
            run_id = str(uuid4())
            state["run"] = {
                "id": run_id,
                "started_at": utc_now_iso(),
                "lease_until": now + lease_seconds,
            }
            atomic_write_json(self._path(), state)
        append_audit("run_begin", run_id=run_id)
        return json_result(
            ok=True,
            acquired=True,
            run_id=run_id,
            report_due=self._report_due(state),
            state=state,
        )

    def _checkpoint(self, args: dict[str, Any]) -> str:
        run_id = str(args.get("run_id", "")).strip()
        if not run_id:
            return json_result(ok=False, error="run_id is required")
        inbox_batch_id = str(args.get("inbox_batch_id", "")).strip()
        has_slack_checkpoint = "last_slack_ts" in args and args.get("last_slack_ts") is not None
        if bool(inbox_batch_id) != has_slack_checkpoint:
            return json_result(
                ok=False,
                error="inbox_batch_id and last_slack_ts must be checkpointed together",
            )
        event_ids: list[str] = []
        if has_slack_checkpoint:
            last_slack_ts = args.get("last_slack_ts")
            if not isinstance(last_slack_ts, str):
                return json_result(ok=False, error="last_slack_ts must be str")
            try:
                event_ids = validate_ready_batch(inbox_batch_id, run_id, last_slack_ts)
            except (OSError, ValueError) as exc:
                return json_result(ok=False, error=str(exc))
        allowed = {
            "board_snapshot": dict,
            "last_slack_ts": str,
            "last_report_at": str,
            "last_notified_digest": str,
        }
        with exclusive_file_lock(self._path()):
            state = self._load()
            active = state.get("run")
            if not isinstance(active, dict) or active.get("id") != run_id:
                return json_result(ok=False, error="run_id does not own the active lease")
            updated: list[str] = []
            for key, expected_type in allowed.items():
                if key not in args or args[key] is None:
                    continue
                value = args[key]
                if not isinstance(value, expected_type):
                    return json_result(ok=False, error=f"{key} must be {expected_type.__name__}")
                if key == "board_snapshot":
                    try:
                        snapshot_size = len(json.dumps(value, ensure_ascii=False))
                    except TypeError:
                        return json_result(ok=False, error="board_snapshot must be JSON serializable")
                    if snapshot_size > 2_000_000:
                        return json_result(ok=False, error="board_snapshot exceeds the 2 MB safety limit")
                    if not SHA256_RE.fullmatch(str(value.get("digest", ""))):
                        return json_result(ok=False, error="board_snapshot.digest must be a SHA-256 hex digest")
                if key == "last_slack_ts":
                    if not SLACK_TS_RE.fullmatch(value):
                        return json_result(ok=False, error="last_slack_ts must be 0 or a Slack timestamp")
                    previous = str(state.get("last_slack_ts", "0"))
                    if SLACK_TS_RE.fullmatch(previous) and float(value) < float(previous):
                        return json_result(ok=False, error="last_slack_ts cannot move backwards")
                if key == "last_notified_digest" and not SHA256_RE.fullmatch(value):
                    return json_result(ok=False, error="last_notified_digest must be a SHA-256 hex digest")
                if key == "last_report_at":
                    try:
                        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    except ValueError:
                        return json_result(ok=False, error="last_report_at must be an ISO-8601 timestamp")
                    if parsed.tzinfo is None:
                        return json_result(ok=False, error="last_report_at must include a timezone")
                state[key] = value
                updated.append(key)
            atomic_write_json(self._path(), state)
        try:
            if has_slack_checkpoint:
                event_ids = consume_ready_batch(inbox_batch_id, run_id, str(args["last_slack_ts"]))
                complete_events(event_ids)
        except (OSError, ValueError) as exc:
            append_audit("state_checkpoint", run_id=run_id, updated=updated, event_ack=False)
            return json_result(
                ok=False,
                checkpoint_saved=True,
                error=f"Slack event acknowledgement failed: {type(exc).__name__}; retry the checkpoint",
            )
        append_audit("state_checkpoint", run_id=run_id, updated=updated)
        return json_result(ok=True, updated=updated, completed_event_count=len(event_ids))

    def _finish(self, args: dict[str, Any]) -> str:
        run_id = str(args.get("run_id", "")).strip()
        if not run_id:
            return json_result(ok=False, error="run_id is required")
        with exclusive_file_lock(self._path()):
            state = self._load()
            active = state.get("run")
            if not isinstance(active, dict) or active.get("id") != run_id:
                return json_result(ok=False, error="run_id does not own the active lease")
            state["run"] = None
            atomic_write_json(self._path(), state)
        append_audit("run_finish", run_id=run_id)
        return json_result(ok=True, finished=True)

    @staticmethod
    def _report_due(state: dict[str, Any]) -> bool:
        last_report_at = state.get("last_report_at")
        if not last_report_at:
            return True
        try:
            last_report = datetime.fromisoformat(str(last_report_at).replace("Z", "+00:00"))
            if last_report.tzinfo is None:
                last_report = last_report.replace(tzinfo=timezone.utc)
            interval_hours = max(1, int(os.getenv("COLLEAGUE_REPORT_INTERVAL_HOURS", "24")))
        except ValueError:
            return True
        elapsed = datetime.now(timezone.utc) - last_report.astimezone(timezone.utc)
        return elapsed.total_seconds() >= interval_hours * 3600
