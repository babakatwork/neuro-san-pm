"""Create deterministic, compact GitHub Project snapshots for change detection."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter
from datetime import datetime
from datetime import timezone
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._runtime import json_result

DONE_STATUSES = {"closed", "complete", "completed", "done", "shipped"}


class KanbanSnapshot(CodedTool):
    """Normalize project items and calculate a stable SHA-256 digest."""

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del sly_data
        raw_items = args.get("items", [])
        if not isinstance(raw_items, list):
            return json_result(ok=False, error="items must be an array")
        try:
            max_items = max(1, int(os.getenv("COLLEAGUE_MAX_PROJECT_ITEMS", "500")))
        except ValueError:
            return json_result(ok=False, error="COLLEAGUE_MAX_PROJECT_ITEMS must be an integer")
        if len(raw_items) > max_items:
            return json_result(ok=False, error=f"items exceeds the configured {max_items} item safety limit")
        items = [self._normalize(item) for item in raw_items if isinstance(item, dict)]
        items.sort(key=lambda item: (item["id"], item["title"]))
        canonical = {
            "project_title": str(args.get("project_title", ""))[:300],
            "project_url": str(args.get("project_url", ""))[:1000],
            "items": items,
        }
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        status_counts = dict(sorted(Counter(item["status"] for item in items).items()))
        try:
            stale_after_days = int(os.getenv("COLLEAGUE_STALE_AFTER_DAYS", "14"))
        except ValueError:
            return json_result(ok=False, error="COLLEAGUE_STALE_AFTER_DAYS must be a positive integer")
        if stale_after_days < 1:
            return json_result(ok=False, error="COLLEAGUE_STALE_AFTER_DAYS must be a positive integer")
        stale = [item for item in items if self._is_stale(item, stale_after_days)]
        blocked = [
            item
            for item in items
            if "block" in item["status"].lower() or any("block" in label.lower() for label in item["labels"])
        ]
        snapshot = {
            **canonical,
            "digest": digest,
            "item_count": len(items),
            "status_counts": status_counts,
            "attention": {
                "blocked": blocked[:50],
                "missing_status_count": status_counts.get("No status", 0),
                "stale": stale[:50],
                "stale_after_days": stale_after_days,
            },
        }
        return json_result(ok=True, snapshot=snapshot)

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)

    @staticmethod
    def _normalize(item: dict[str, Any]) -> dict[str, Any]:
        title = str(item.get("title", ""))[:500]
        url = str(item.get("url", ""))[:1000]
        identity = str(item.get("id") or url or title)[:1000]
        status = str(item.get("status") or "No status")[:200]
        return {
            "id": identity,
            "type": str(item.get("type", "Issue"))[:100],
            "number": str(item.get("number", ""))[:100],
            "title": title,
            "url": url,
            "status": status,
            "priority": str(item.get("priority", ""))[:200],
            "assignees": KanbanSnapshot._string_list(item.get("assignees")),
            "labels": KanbanSnapshot._string_list(item.get("labels")),
            "updated_at": str(item.get("updated_at", ""))[:100],
        }

    @staticmethod
    def _is_stale(item: dict[str, Any], stale_after_days: int) -> bool:
        if item["status"].strip().lower() in DONE_STATUSES:
            return False
        raw = item.get("updated_at")
        if not raw:
            return False
        try:
            updated = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        age = datetime.now(timezone.utc) - updated.astimezone(timezone.utc)
        return age.days >= stale_after_days

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return sorted({str(item)[:200] for item in value if item})
