import json

from coded_tools.colleague.kanban_snapshot import KanbanSnapshot


def test_snapshot_is_deterministic_and_surfaces_attention(monkeypatch):
    monkeypatch.setenv("COLLEAGUE_STALE_AFTER_DAYS", "14")
    items = [
        {
            "id": "2",
            "title": "Blocked work",
            "status": "Blocked",
            "labels": ["bug"],
            "updated_at": "2000-01-01T00:00:00Z",
        },
        {
            "id": "1",
            "title": "Done work",
            "status": "Done",
            "updated_at": "2000-01-01T00:00:00Z",
        },
        {"id": "3", "title": "Needs triage"},
    ]
    tool = KanbanSnapshot()

    first = json.loads(tool.invoke({"project_title": "Roadmap", "items": items}, {}))["snapshot"]
    second = json.loads(tool.invoke({"project_title": "Roadmap", "items": list(reversed(items))}, {}))["snapshot"]

    assert first["digest"] == second["digest"]
    assert first["status_counts"] == {"Blocked": 1, "Done": 1, "No status": 1}
    assert first["priority_counts"] == {"No priority": 3}
    assert first["missing_assignee_count"] == 3
    assert "items" not in first
    assert first["attention"]["missing_status_count"] == 1
    assert first["attention"]["blocked_count"] == 1
    assert first["attention"]["stale_count"] == 1
    assert [item["id"] for item in first["attention"]["blocked"]] == ["2"]
    assert {item["id"] for item in first["attention"]["stale"]} == {"2"}


def test_snapshot_enforces_item_limit(monkeypatch):
    monkeypatch.setenv("COLLEAGUE_MAX_PROJECT_ITEMS", "1")
    result = json.loads(KanbanSnapshot().invoke({"items": [{"title": "one"}, {"title": "two"}]}, {}))
    assert result["ok"] is False
    assert "safety limit" in result["error"]


def test_snapshot_rejects_invalid_stale_policy(monkeypatch):
    monkeypatch.setenv("COLLEAGUE_STALE_AFTER_DAYS", "not-a-number")

    result = json.loads(KanbanSnapshot().invoke({"items": []}, {}))

    assert result["ok"] is False
    assert "positive integer" in result["error"]
