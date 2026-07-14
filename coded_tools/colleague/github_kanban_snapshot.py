"""Read the configured GitHub Project and compact it before exposing data to an LLM."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._runtime import append_audit
from coded_tools.colleague._runtime import json_result
from coded_tools.colleague.github_project_reader import GitHubProjectReader
from coded_tools.colleague.kanban_snapshot import KanbanSnapshot


class GitHubKanbanSnapshot(CodedTool):
    """Perform the raw Project read and deterministic compaction inside the host."""

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del args
        try:
            project_result = json.loads(GitHubProjectReader().invoke({}, sly_data))
        except (TypeError, ValueError):
            append_audit("github_kanban_snapshot", ok=False, error_code="invalid_reader_result")
            return json_result(ok=False, error="GitHub Project reader returned an invalid result")
        if not project_result.get("ok"):
            append_audit("github_kanban_snapshot", ok=False, error_code="project_read_failed")
            return json_result(ok=False, error=project_result.get("error", "GitHub Project read failed"))
        if project_result.get("complete") is not True:
            append_audit("github_kanban_snapshot", ok=False, error_code="incomplete_project_read")
            return json_result(ok=False, error="GitHub Project read was incomplete")

        project = project_result.get("project")
        items = project_result.get("items")
        if not isinstance(project, dict) or not isinstance(items, list):
            append_audit("github_kanban_snapshot", ok=False, error_code="invalid_project_result")
            return json_result(ok=False, error="GitHub Project reader returned an invalid result")

        try:
            snapshot_result = json.loads(
                KanbanSnapshot().invoke(
                    {
                        "project_title": project.get("title", ""),
                        "project_url": project.get("url", ""),
                        "items": items,
                    },
                    sly_data,
                )
            )
        except (TypeError, ValueError):
            append_audit("github_kanban_snapshot", ok=False, error_code="invalid_snapshot_result")
            return json_result(ok=False, error="Kanban snapshot returned an invalid result")
        if not snapshot_result.get("ok") or not isinstance(snapshot_result.get("snapshot"), dict):
            append_audit("github_kanban_snapshot", ok=False, error_code="snapshot_failed")
            return json_result(ok=False, error=snapshot_result.get("error", "Kanban snapshot failed"))

        snapshot = snapshot_result["snapshot"]
        append_audit(
            "github_kanban_snapshot",
            ok=True,
            item_count=snapshot.get("item_count", 0),
            attention_item_count=(
                len(snapshot.get("attention", {}).get("blocked", []))
                + len(snapshot.get("attention", {}).get("stale", []))
            ),
        )
        return json_result(ok=True, snapshot=snapshot)

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)
