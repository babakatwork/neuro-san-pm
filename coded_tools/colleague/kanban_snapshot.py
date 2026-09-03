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
MAX_ATTENTION_ITEMS = 20
MAX_ORDERED_ITEMS_PER_COLUMN = 20


class KanbanSnapshot(CodedTool):
    """Normalize project items and calculate a stable SHA-256 digest."""

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del sly_data
        raw_items = args.get("items", [])
        if not isinstance(raw_items, list):
            return json_result(ok=False, error="items must be an array")
        try:
            max_items = max(1, int(os.getenv("COLLEAGUE_MAX_PROJECT_ITEMS", "10000")))
        except ValueError:
            return json_result(ok=False, error="COLLEAGUE_MAX_PROJECT_ITEMS must be an integer")
        if len(raw_items) > max_items:
            return json_result(ok=False, error=f"items exceeds the configured {max_items} item safety limit")
        items = [self._normalize(item) for item in raw_items if isinstance(item, dict)]
        items.sort(key=self._project_order_key)
        canonical = {
            "project_title": str(args.get("project_title", ""))[:300],
            "project_url": str(args.get("project_url", ""))[:1000],
            "items": items,
        }
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        status_counts = dict(sorted(Counter(item["status"] for item in items).items()))
        priority_counts = dict(sorted(Counter(item["priority"] or "No priority" for item in items).items()))
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
        active_items = [item for item in items if item["status"].strip().lower() not in DONE_STATUSES]
        ordered_columns = self._ordered_columns(items)
        snapshot = {
            "project_title": canonical["project_title"],
            "project_url": canonical["project_url"],
            "digest": digest,
            "item_count": len(items),
            "status_counts": status_counts,
            "priority_counts": priority_counts,
            "missing_assignee_count": sum(not item["assignees"] for item in items),
            "assignee_counts": self._assignee_counts(items),
            "active_assignee_counts": self._assignee_counts(active_items),
            "active_missing_assignee_count": sum(not item["assignees"] for item in active_items),
            "ordered_columns": ordered_columns,
            "attention": {
                "blocked": blocked[:MAX_ATTENTION_ITEMS],
                "blocked_count": len(blocked),
                "missing_status_count": status_counts.get("No status", 0),
                "stale": stale[:MAX_ATTENTION_ITEMS],
                "stale_count": len(stale),
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
        raw_position = item.get("project_position")
        project_position = raw_position if isinstance(raw_position, int) and raw_position > 0 else None
        return {
            "id": identity,
            "type": str(item.get("type", "Issue"))[:100],
            "repository": str(item.get("repository", ""))[:300],
            "number": str(item.get("number", ""))[:100],
            "title": title,
            "url": url,
            "status": status,
            "priority": str(item.get("priority", ""))[:200],
            "project_position": project_position,
            "assignees": KanbanSnapshot._string_list(item.get("assignees")),
            "labels": KanbanSnapshot._string_list(item.get("labels")),
            "updated_at": str(item.get("updated_at", ""))[:100],
        }

    @staticmethod
    def _project_order_key(item: dict[str, Any]) -> tuple[int, int, str, str]:
        """Keep the GitHub project position, with deterministic fallback for legacy input."""
        position = item.get("project_position")
        if isinstance(position, int):
            return (0, position, item["id"], item["title"])
        return (1, 0, item["id"], item["title"])

    @staticmethod
    def _ordered_columns(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Expose a bounded top-of-column view while preserving full-board order in the digest."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            grouped.setdefault(item["status"], []).append(item)

        columns: dict[str, dict[str, Any]] = {}
        for status in sorted(grouped):
            column_items = grouped[status]
            visible = []
            for rank, item in enumerate(column_items[:MAX_ORDERED_ITEMS_PER_COLUMN], start=1):
                visible.append(
                    {
                        "rank": rank,
                        "project_position": item["project_position"],
                        "type": item["type"],
                        "repository": item["repository"],
                        "number": item["number"],
                        "title": item["title"],
                        "url": item["url"],
                        "assignees": item["assignees"],
                    }
                )
            columns[status] = {
                "item_count": len(column_items),
                "items": visible,
                "truncated": len(column_items) > MAX_ORDERED_ITEMS_PER_COLUMN,
            }
        return columns

    @staticmethod
    def _assignee_counts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return the complete per-login distribution in deterministic rank order."""
        counts = Counter(assignee for item in items for assignee in item["assignees"])
        return [
            {"login": login, "ticket_count": count}
            for login, count in sorted(
                counts.items(),
                key=lambda value: (-value[1], value[0].casefold(), value[0]),
            )
        ]

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
