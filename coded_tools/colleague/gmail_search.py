"""Bounded, host-scoped Gmail search returning metadata only."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._gmail_client import READ_SCOPE
from coded_tools.colleague._gmail_client import GmailConfigurationError
from coded_tools.colleague._gmail_client import build_gmail_service
from coded_tools.colleague._runtime import env_bool
from coded_tools.colleague._runtime import json_result

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_HEADERS = ("From", "To", "Subject", "Date", "Message-ID")


def _headers(message: dict[str, Any]) -> dict[str, str]:
    wanted = {name.lower(): name for name in _SAFE_HEADERS}
    result: dict[str, str] = {}
    for header in message.get("payload", {}).get("headers", []):
        name = str(header.get("name", "")).lower()
        if name in wanted:
            result[wanted[name]] = str(header.get("value", ""))[:1000]
    return result


class GmailSearch(CodedTool):
    """Search a fixed authenticated mailbox and return bounded metadata."""

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del sly_data
        if not env_bool("COLLEAGUE_GMAIL_ENABLED", False):
            return json_result(ok=False, error="Gmail access is disabled")
        query = str(args.get("query", "")).strip()
        if not query or len(query) > 500:
            return json_result(ok=False, error="query is required and must not exceed 500 characters")
        try:
            limit = int(args.get("limit", 10))
        except (TypeError, ValueError):
            return json_result(ok=False, error="limit must be an integer")
        limit = min(max(limit, 1), 20)
        prefix = os.getenv("GMAIL_QUERY_PREFIX", "in:inbox newer_than:30d").strip()
        effective_query = f"{prefix} ({query})" if prefix else query
        try:
            service = build_gmail_service({READ_SCOPE})
            listing = service.users().messages().list(
                userId="me", q=effective_query, maxResults=limit, includeSpamTrash=False
            ).execute()
            messages = []
            for ref in listing.get("messages", [])[:limit]:
                message_id = str(ref.get("id", ""))
                if not _ID_RE.fullmatch(message_id):
                    continue
                item = service.users().messages().get(
                    userId="me", id=message_id, format="metadata", metadataHeaders=list(_SAFE_HEADERS)
                ).execute()
                messages.append({
                    "id": message_id,
                    "thread_id": str(item.get("threadId", "")),
                    "headers": _headers(item),
                    "snippet": str(item.get("snippet", ""))[:1000],
                    "labels": [str(value) for value in item.get("labelIds", [])[:20]],
                })
        except GmailConfigurationError as exc:
            return json_result(ok=False, error=str(exc))
        except Exception:
            return json_result(ok=False, error="Gmail search failed")
        return json_result(ok=True, query_applied=effective_query, count=len(messages), messages=messages)

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)
