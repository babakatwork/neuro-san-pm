"""Post a bounded, deduplicated message to one fixed Slack channel."""

from __future__ import annotations

import asyncio
import hashlib
import html
import os
import time
from pathlib import Path
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._runtime import append_audit
from coded_tools.colleague._runtime import atomic_write_json
from coded_tools.colleague._runtime import env_bool
from coded_tools.colleague._runtime import exclusive_file_lock
from coded_tools.colleague._runtime import has_active_lease
from coded_tools.colleague._runtime import json_result
from coded_tools.colleague._runtime import read_json
from coded_tools.colleague._slack_client import SlackApiClient
from coded_tools.colleague._slack_client import SlackApiError
from coded_tools.colleague.slack_inbox_batch import mark_delivered
from coded_tools.colleague.slack_inbox_batch import reply_thread


class SlackPost(CodedTool):
    """Send through a fixed channel boundary; the model cannot choose a destination."""

    @staticmethod
    def _delivery_path() -> Path:
        state_path = Path(os.getenv("COLLEAGUE_STATE_PATH", ".state/colleague.json"))
        return state_path.with_name("slack_delivery.json")

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del sly_data
        channel = os.getenv("SLACK_CHANNEL_ID", "").strip()
        text = str(args.get("text", "")).strip()
        run_id = str(args.get("run_id", "")).strip()
        inbox_batch_id = str(args.get("inbox_batch_id", "")).strip()
        reply_to_ts = str(args.get("reply_to_ts", "")).strip()
        if not channel:
            return json_result(ok=False, sent=False, error="SLACK_CHANNEL_ID is not configured")
        if not text:
            return json_result(ok=False, sent=False, error="text is required")
        if len(text) > 3500:
            return json_result(ok=False, sent=False, error="text exceeds the 3500 character safety limit")
        if not has_active_lease(run_id):
            return json_result(ok=False, sent=False, error="run_id does not own an active colleague lease")
        if bool(inbox_batch_id) != bool(reply_to_ts):
            return json_result(
                ok=False,
                sent=False,
                error="inbox_batch_id and reply_to_ts must be supplied together",
            )
        try:
            thread_ts = reply_thread(inbox_batch_id, run_id, reply_to_ts) if inbox_batch_id else None
        except (OSError, ValueError) as exc:
            return json_result(ok=False, sent=False, error=str(exc))

        prefix = os.getenv("COLLEAGUE_SLACK_MESSAGE_PREFIX", "[neuro-san colleague]").strip()
        # Slack mention/control syntax is angle-bracket based. Escape the whole
        # model-produced message and disable mrkdwn/unfurls before it crosses
        # the host-owned notification boundary.
        outgoing_text = html.escape(f"{prefix} {text}" if prefix else text, quote=False)
        fingerprint = hashlib.sha256(f"{channel}\n{thread_ts}\n{outgoing_text}".encode()).hexdigest()

        if not env_bool("COLLEAGUE_SLACK_WRITE_ENABLED", False):
            append_audit("slack_post", sent=False, dry_run=True, message_sha256=fingerprint)
            return json_result(
                ok=True,
                sent=False,
                dry_run=True,
                message_sha256=fingerprint,
                preview=outgoing_text,
            )

        token = os.getenv("SLACK_BOT_TOKEN", "").strip()
        if not token:
            return json_result(ok=False, sent=False, error="SLACK_BOT_TOKEN is not configured")

        now = time.time()
        try:
            dedupe_seconds = int(os.getenv("COLLEAGUE_SLACK_DEDUPE_SECONDS", "21600"))
        except ValueError:
            return json_result(ok=False, sent=False, error="COLLEAGUE_SLACK_DEDUPE_SECONDS must be an integer")
        if dedupe_seconds < 0:
            return json_result(ok=False, sent=False, error="COLLEAGUE_SLACK_DEDUPE_SECONDS must be non-negative")
        delivery_path = self._delivery_path()
        try:
            with exclusive_file_lock(delivery_path):
                delivery = read_json(delivery_path, {"sent": {}})
                sent = delivery.setdefault("sent", {})
                if not isinstance(sent, dict):
                    raise ValueError("Slack delivery state is invalid")
                sent = {
                    key: value
                    for key, value in sent.items()
                    if isinstance(value, (int, float)) and now - float(value) <= dedupe_seconds
                }
                delivery["sent"] = sent
                if fingerprint in sent:
                    if inbox_batch_id:
                        mark_delivered(inbox_batch_id, run_id, reply_to_ts)
                    append_audit("slack_post", sent=False, duplicate=True, message_sha256=fingerprint)
                    return json_result(ok=True, sent=False, duplicate=True, message_sha256=fingerprint)

                payload: dict[str, Any] = {
                    "channel": channel,
                    "text": outgoing_text,
                    "mrkdwn": False,
                    "unfurl_links": False,
                    "unfurl_media": False,
                }
                if thread_ts:
                    payload["thread_ts"] = thread_ts
                body = SlackApiClient(token).call("chat.postMessage", http_method="POST", payload=payload)
                sent[fingerprint] = now
                atomic_write_json(delivery_path, delivery)
            if inbox_batch_id:
                mark_delivered(inbox_batch_id, run_id, reply_to_ts)
        except (OSError, TypeError, ValueError, SlackApiError) as exc:
            error = str(exc) if isinstance(exc, (SlackApiError, ValueError)) else "Slack delivery state is unavailable"
            append_audit("slack_post", sent=False, error=error, message_sha256=fingerprint)
            return json_result(ok=False, sent=False, error=error, message_sha256=fingerprint)

        message_ts = str(body.get("ts", ""))
        append_audit("slack_post", sent=True, message_sha256=fingerprint, message_ts=message_ts)
        return json_result(ok=True, sent=True, message_sha256=fingerprint, message_ts=message_ts)

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)
