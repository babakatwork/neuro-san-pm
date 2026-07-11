"""Allowlisted, lease-bound and deduplicated Gmail sending."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._gmail_client import SEND_SCOPE
from coded_tools.colleague._gmail_client import GmailConfigurationError
from coded_tools.colleague._gmail_client import build_gmail_service
from coded_tools.colleague._runtime import append_audit
from coded_tools.colleague._runtime import atomic_write_json
from coded_tools.colleague._runtime import env_bool
from coded_tools.colleague._runtime import exclusive_file_lock
from coded_tools.colleague._runtime import has_active_lease
from coded_tools.colleague._runtime import json_result
from coded_tools.colleague._runtime import read_json

_EMAIL_RE = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")


class GmailSend(CodedTool):
    """Send plain text only to host-allowlisted recipients."""

    @staticmethod
    def _delivery_path() -> Path:
        return Path(os.getenv("COLLEAGUE_STATE_PATH", ".state/colleague.json")).with_name("gmail_delivery.json")

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del sly_data
        run_id = str(args.get("run_id", "")).strip()
        recipient = str(args.get("to", "")).strip().lower()
        subject = str(args.get("subject", "")).strip()
        body = str(args.get("body", "")).strip()
        allowed = {
            item.strip().lower()
            for item in os.getenv("GMAIL_ALLOWED_RECIPIENTS", "").split(",")
            if item.strip()
        }
        if not env_bool("COLLEAGUE_GMAIL_ENABLED", False):
            return json_result(ok=False, sent=False, error="Gmail access is disabled")
        if not has_active_lease(run_id):
            return json_result(ok=False, sent=False, error="run_id does not own an active colleague lease")
        if not _EMAIL_RE.fullmatch(recipient) or recipient not in allowed:
            return json_result(ok=False, sent=False, error="recipient is not in GMAIL_ALLOWED_RECIPIENTS")
        if not subject or len(subject) > 200 or "\n" in subject or "\r" in subject:
            return json_result(ok=False, sent=False, error="subject is invalid or exceeds 200 characters")
        if not body or len(body) > 10000:
            return json_result(ok=False, sent=False, error="body is required and must not exceed 10000 characters")
        fingerprint = hashlib.sha256(f"{recipient}\n{subject}\n{body}".encode()).hexdigest()
        if not env_bool("COLLEAGUE_GMAIL_WRITE_ENABLED", False):
            append_audit("gmail_send", sent=False, dry_run=True, message_sha256=fingerprint)
            return json_result(ok=True, sent=False, dry_run=True, message_sha256=fingerprint,
                               preview={"to": recipient, "subject": subject, "body": body})

        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        now = time.time()
        path = self._delivery_path()
        try:
            with exclusive_file_lock(path):
                delivery = read_json(path, {"sent": {}})
                sent = delivery.get("sent", {})
                if not isinstance(sent, dict):
                    raise ValueError("Gmail delivery state is invalid")
                sent = {
                    key: value
                    for key, value in sent.items()
                    if isinstance(value, (int, float)) and now - float(value) <= 86400
                }
                if fingerprint in sent:
                    return json_result(ok=True, sent=False, duplicate=True, message_sha256=fingerprint)
                result = build_gmail_service({SEND_SCOPE}).users().messages().send(
                    userId="me", body={"raw": raw}
                ).execute()
                sent[fingerprint] = now
                atomic_write_json(path, {"sent": sent})
        except GmailConfigurationError as exc:
            return json_result(ok=False, sent=False, error=str(exc), message_sha256=fingerprint)
        except Exception:
            return json_result(ok=False, sent=False, error="Gmail send failed", message_sha256=fingerprint)
        message_id = str(result.get("id", ""))
        append_audit("gmail_send", sent=True, message_sha256=fingerprint, message_id=message_id)
        return json_result(ok=True, sent=True, message_sha256=fingerprint, message_id=message_id)

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)
