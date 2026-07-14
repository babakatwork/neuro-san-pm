import json
from datetime import datetime
from datetime import timezone

from coded_tools.colleague.colleague_state import ColleagueState
from coded_tools.colleague.run_finalizer import RunFinalizer
from coded_tools.colleague.slack_inbox_batch import create_batch


def _begin(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLEAGUE_STATE_PATH", str(tmp_path / "colleague.json"))
    monkeypatch.setenv("COLLEAGUE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    return json.loads(ColleagueState().invoke({"action": "begin"}, {}))["run_id"]


def test_silent_run_still_checkpoints_board_and_inbox(monkeypatch, tmp_path):
    run_id = _begin(monkeypatch, tmp_path)
    batch_id = create_batch(run_id, "20.0", [])
    snapshot = {"digest": "a" * 64, "item_count": 355}

    result = json.loads(
        RunFinalizer().invoke(
            {
                "run_id": run_id,
                "board_snapshot": snapshot,
                "inbox_batch_id": batch_id,
                "checkpoint_ts": "20.0",
            },
            {},
        )
    )

    state = json.loads(ColleagueState().invoke({"action": "read"}, {}))["state"]
    assert result["ok"] is True
    assert result["slack_update"]["skipped"] is True
    assert state["board_snapshot"] == snapshot
    assert state["last_slack_ts"] == "20.0"
    assert state["run"] is None


def test_chosen_slack_update_and_changed_board_email_are_recorded(monkeypatch, tmp_path):
    first_run = _begin(monkeypatch, tmp_path)
    state_tool = ColleagueState()
    state_tool.invoke(
        {"action": "checkpoint", "run_id": first_run, "board_snapshot": {"digest": "a" * 64}},
        {},
    )
    state_tool.invoke({"action": "finish", "run_id": first_run}, {})
    run_id = json.loads(state_tool.invoke({"action": "begin"}, {}))["run_id"]
    monkeypatch.setenv("COLLEAGUE_DAILY_SUMMARY_TO", "owner@example.com")
    monkeypatch.setattr(
        "coded_tools.colleague.run_finalizer.SlackPost.invoke",
        lambda self, args, sly_data: json.dumps({"ok": True, "sent": True, "message_ts": "30.0"}),
    )
    monkeypatch.setattr(
        "coded_tools.colleague.run_finalizer.GmailSend.invoke",
        lambda self, args, sly_data: json.dumps({"ok": True, "sent": True, "message_id": "mail-1"}),
    )

    result = json.loads(
        RunFinalizer().invoke(
            {
                "run_id": run_id,
                "board_snapshot": {"digest": "b" * 64},
                "slack_update": "A focused product update.",
                "email_summary": {"subject": "Daily neuro-san summary", "body": "One change."},
            },
            {},
        )
    )

    state = json.loads(state_tool.invoke({"action": "read"}, {}))["state"]
    assert result["board_changed"] is True
    assert result["slack_update"]["sent"] is True
    assert result["email_summary"]["sent"] is True
    assert state["last_notified_digest"] == "b" * 64
    assert state["daily_email_pending"] is False
    assert state["last_email_summary_at"]


def test_second_change_same_day_remains_pending_without_second_email(monkeypatch, tmp_path):
    run_id = _begin(monkeypatch, tmp_path)
    state_tool = ColleagueState()
    state_tool.invoke(
        {
            "action": "checkpoint",
            "run_id": run_id,
            "board_snapshot": {"digest": "a" * 64},
            "last_email_summary_at": datetime.now(timezone.utc).isoformat(),
        },
        {},
    )
    state_tool.invoke({"action": "finish", "run_id": run_id}, {})
    run_id = json.loads(state_tool.invoke({"action": "begin"}, {}))["run_id"]
    monkeypatch.setenv("COLLEAGUE_DAILY_SUMMARY_TO", "owner@example.com")

    result = json.loads(
        RunFinalizer().invoke(
            {
                "run_id": run_id,
                "board_snapshot": {"digest": "b" * 64},
                "email_summary": {"subject": "Daily summary", "body": "Changed."},
            },
            {},
        )
    )

    assert result["email_summary"]["reason"] == "a daily summary was already sent today"
    state = json.loads(state_tool.invoke({"action": "read"}, {}))["state"]
    assert state["daily_email_pending"] is True
