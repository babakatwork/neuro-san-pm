"""Deterministically deliver chosen communications and finish a colleague run."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from datetime import timezone
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._runtime import append_audit
from coded_tools.colleague._runtime import json_result
from coded_tools.colleague._runtime import utc_now_iso
from coded_tools.colleague.colleague_state import ColleagueState
from coded_tools.colleague.gmail_send import GmailSend
from coded_tools.colleague.slack_post import SlackPost


def _result(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("tool returned a non-object result")
    return value


def _delivered(value: dict[str, Any]) -> bool:
    return bool(value.get("sent") or value.get("duplicate"))


def _same_utc_day(value: object, now: datetime) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date() == now.date()


class RunFinalizer(CodedTool):
    """Apply host-owned delivery/checkpoint rules and always release the lease."""

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del sly_data
        run_id = str(args.get("run_id", "")).strip()
        if not run_id:
            return json_result(ok=False, finalized=False, error="run_id is required")

        state_tool = ColleagueState()
        try:
            state_result = _result(state_tool.invoke({"action": "read"}, {}))
            state = state_result.get("state", {})
            active = state.get("run") if isinstance(state, dict) else None
            if not isinstance(active, dict) or active.get("id") != run_id:
                return json_result(ok=False, finalized=False, error="run_id does not own the active lease")

            snapshot = args.get("board_snapshot")
            if snapshot is not None and not isinstance(snapshot, dict):
                return self._finish_with_error(state_tool, run_id, "board_snapshot must be an object")
            digest = str(snapshot.get("digest", "")) if isinstance(snapshot, dict) else ""
            previous = state.get("board_snapshot")
            previous_digest = str(previous.get("digest", "")) if isinstance(previous, dict) else ""
            board_changed = bool(previous_digest and digest and previous_digest != digest)
            daily_email_pending = bool(state.get("daily_email_pending")) or board_changed
            now = datetime.now(timezone.utc)
            now_iso = utc_now_iso()

            slack_update = str(args.get("slack_update", "")).strip()
            slack_result: dict[str, Any] = {"skipped": True, "reason": "agent chose no update"}
            if slack_update:
                slack_result = _result(SlackPost().invoke({"run_id": run_id, "text": slack_update}, {}))

            replies = args.get("request_replies", [])
            if not isinstance(replies, list):
                return self._finish_with_error(state_tool, run_id, "request_replies must be an array")
            inbox_batch_id = str(args.get("inbox_batch_id", "")).strip()
            checkpoint_ts = str(args.get("checkpoint_ts", "")).strip()
            if bool(inbox_batch_id) != bool(checkpoint_ts):
                return self._finish_with_error(
                    state_tool,
                    run_id,
                    "inbox_batch_id and checkpoint_ts must be supplied together",
                )
            reply_results: list[dict[str, Any]] = []
            for reply in replies:
                if not isinstance(reply, dict):
                    reply_results.append({"ok": False, "sent": False, "error": "reply must be an object"})
                    continue
                request_ts = str(reply.get("request_ts", "")).strip()
                text = str(reply.get("text", "")).strip()
                reply_results.append(
                    _result(
                        SlackPost().invoke(
                            {
                                "run_id": run_id,
                                "text": text,
                                "inbox_batch_id": inbox_batch_id,
                                "reply_to_ts": request_ts,
                            },
                            {},
                        )
                    )
                )

            email_result: dict[str, Any] = {"skipped": True, "reason": "agent chose no summary"}
            email_summary = args.get("email_summary")
            summary_recipient = os.getenv("COLLEAGUE_DAILY_SUMMARY_TO", "").strip().lower()
            summary_sent_today = _same_utc_day(state.get("last_email_summary_at"), now)
            if email_summary is not None:
                if not isinstance(email_summary, dict):
                    return self._finish_with_error(state_tool, run_id, "email_summary must be an object")
                if not daily_email_pending:
                    email_result = {"skipped": True, "reason": "no board change is awaiting a summary"}
                elif summary_sent_today:
                    email_result = {"skipped": True, "reason": "a daily summary was already sent today"}
                elif not summary_recipient:
                    email_result = {"skipped": True, "reason": "COLLEAGUE_DAILY_SUMMARY_TO is not configured"}
                else:
                    email_result = _result(
                        GmailSend().invoke(
                            {
                                "run_id": run_id,
                                "to": summary_recipient,
                                "subject": str(email_summary.get("subject", "")).strip(),
                                "body": str(email_summary.get("body", "")).strip(),
                            },
                            {},
                        )
                    )
                    if _delivered(email_result):
                        daily_email_pending = False

            checkpoint_args: dict[str, Any] = {
                "action": "checkpoint",
                "run_id": run_id,
                "daily_email_pending": daily_email_pending,
            }
            if snapshot is not None:
                checkpoint_args["board_snapshot"] = snapshot
            if _delivered(slack_result) and digest:
                checkpoint_args["last_report_at"] = now_iso
                checkpoint_args["last_notified_digest"] = digest
            if _delivered(email_result):
                checkpoint_args["last_email_summary_at"] = now_iso
            board_checkpoint = _result(state_tool.invoke(checkpoint_args, {}))

            inbox_checkpoint: dict[str, Any] = {"skipped": True, "reason": "no safe inbox checkpoint"}
            if inbox_batch_id and checkpoint_ts and all(_delivered(item) for item in reply_results):
                inbox_checkpoint = _result(
                    state_tool.invoke(
                        {
                            "action": "checkpoint",
                            "run_id": run_id,
                            "inbox_batch_id": inbox_batch_id,
                            "last_slack_ts": checkpoint_ts,
                        },
                        {},
                    )
                )

            finish = _result(state_tool.invoke({"action": "finish", "run_id": run_id}, {}))
            ok = bool(board_checkpoint.get("ok") and finish.get("ok"))
            append_audit(
                "run_finalizer",
                run_id=run_id,
                ok=ok,
                board_changed=board_changed,
                slack_update_delivered=_delivered(slack_result),
                email_summary_delivered=_delivered(email_result),
                reply_count=len(reply_results),
                inbox_advanced=bool(inbox_checkpoint.get("ok")),
            )
            return json_result(
                ok=ok,
                finalized=bool(finish.get("ok")),
                board_changed=board_changed,
                slack_update=slack_result,
                request_replies=reply_results,
                email_summary=email_result,
                board_checkpoint=board_checkpoint,
                inbox_checkpoint=inbox_checkpoint,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._finish_with_error(state_tool, run_id, f"finalization failed: {type(exc).__name__}")

    @staticmethod
    def _finish_with_error(state_tool: ColleagueState, run_id: str, error: str) -> str:
        finish = _result(state_tool.invoke({"action": "finish", "run_id": run_id}, {}))
        append_audit("run_finalizer", run_id=run_id, ok=False, error_type="validation")
        return json_result(ok=False, finalized=bool(finish.get("ok")), error=error)

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)
