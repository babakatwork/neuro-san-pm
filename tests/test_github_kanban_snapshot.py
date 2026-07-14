import json

from coded_tools.colleague.github_kanban_snapshot import GitHubKanbanSnapshot
from coded_tools.colleague.github_project_reader import GitHubProjectReader


def test_composite_tool_compacts_large_board_before_returning(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLEAGUE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("COLLEAGUE_MAX_PROJECT_ITEMS", "500")
    monkeypatch.setenv("COLLEAGUE_STALE_AFTER_DAYS", "14")
    items = [
        {
            "id": f"item-{index:03d}",
            "title": f"Routine item {index}",
            "status": "Backlog",
            "priority": "P2",
            "assignees": ["owner"],
            "updated_at": "2026-07-14T00:00:00Z",
        }
        for index in range(355)
    ]
    items[0].update(
        {
            "title": "Blocked delivery item",
            "status": "Blocked",
            "assignees": [],
            "updated_at": "2000-01-01T00:00:00Z",
        }
    )
    reader_result = {
        "ok": True,
        "complete": True,
        "project": {"title": "neuro-san kanban board", "url": "https://example.test/project/6"},
        "items": items,
        "item_count": len(items),
    }
    monkeypatch.setattr(
        GitHubProjectReader,
        "invoke",
        lambda self, args, sly_data: json.dumps(reader_result),
    )

    raw = GitHubKanbanSnapshot().invoke({"request": "snapshot_configured_project"}, {})
    result = json.loads(raw)
    snapshot = result["snapshot"]

    assert result["ok"] is True
    assert snapshot["item_count"] == 355
    assert snapshot["status_counts"] == {"Backlog": 354, "Blocked": 1}
    assert snapshot["priority_counts"] == {"P2": 355}
    assert snapshot["missing_assignee_count"] == 1
    assert snapshot["attention"]["blocked_count"] == 1
    assert snapshot["attention"]["blocked"][0]["id"] == "item-000"
    assert "items" not in snapshot
    assert "Routine item 354" not in raw
    assert len(raw) < 20_000


def test_composite_tool_propagates_sanitized_reader_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLEAGUE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(
        GitHubProjectReader,
        "invoke",
        lambda self, args, sly_data: json.dumps({"ok": False, "error": "Configured project is inaccessible"}),
    )

    result = json.loads(GitHubKanbanSnapshot().invoke({"request": "snapshot_configured_project"}, {}))

    assert result == {"error": "Configured project is inaccessible", "ok": False}
