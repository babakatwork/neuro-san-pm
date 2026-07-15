import json
from datetime import datetime
from datetime import timezone

import pytest

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


@pytest.mark.parametrize("slack_update", [None, "NONE", " none ", "null"])
def test_null_or_sentinel_slack_update_is_silent(monkeypatch, tmp_path, slack_update):
    run_id = _begin(monkeypatch, tmp_path)

    def unexpected_post(*args, **kwargs):
        del args, kwargs
        raise AssertionError("SlackPost must not receive an empty draft")

    monkeypatch.setattr("coded_tools.colleague.run_finalizer.SlackPost.invoke", unexpected_post)
    result = json.loads(
        RunFinalizer().invoke(
            {
                "run_id": run_id,
                "board_snapshot": {"digest": "a" * 64},
                "slack_update": slack_update,
            },
            {},
        )
    )

    assert result["ok"] is True
    assert result["slack_update"] == {"skipped": True, "reason": "agent chose no update"}


def test_chosen_slack_update_and_changed_board_email_are_recorded(monkeypatch, tmp_path):
    first_run = _begin(monkeypatch, tmp_path)
    state_tool = ColleagueState()
    state_tool.invoke(
        {"action": "checkpoint", "run_id": first_run, "board_snapshot": {"digest": "a" * 64}},
        {},
    )
    state_tool.invoke({"action": "finish", "run_id": first_run}, {})
    run_id = json.loads(state_tool.invoke({"action": "begin"}, {}))["run_id"]
    monkeypatch.setenv("GMAIL_ALLOWED_RECIPIENTS", "owner@example.com,team@example.com")
    monkeypatch.setenv("COLLEAGUE_DAILY_SUMMARY_TO", "owner@example.com,team@example.com")
    recipients = []
    monkeypatch.setattr(
        "coded_tools.colleague.run_finalizer.SlackPost.invoke",
        lambda self, args, sly_data: json.dumps({"ok": True, "sent": True, "message_ts": "30.0"}),
    )
    monkeypatch.setattr(
        "coded_tools.colleague.run_finalizer.GmailSend.invoke",
        lambda self, args, sly_data: (
            recipients.append(args["to"])
            or json.dumps({"ok": True, "sent": True, "message_id": f"mail-{len(recipients)}"})
        ),
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
    assert result["email_summary"]["delivered"] is True
    assert result["email_summary"]["recipient_count"] == 2
    assert recipients == ["owner@example.com", "team@example.com"]
    assert state["last_notified_digest"] == "b" * 64
    assert state["daily_email_pending"] is False
    assert state["last_email_summary_at"]


def test_partial_daily_summary_delivery_remains_pending(monkeypatch, tmp_path):
    first_run = _begin(monkeypatch, tmp_path)
    state_tool = ColleagueState()
    state_tool.invoke(
        {"action": "checkpoint", "run_id": first_run, "board_snapshot": {"digest": "a" * 64}},
        {},
    )
    state_tool.invoke({"action": "finish", "run_id": first_run}, {})
    run_id = json.loads(state_tool.invoke({"action": "begin"}, {}))["run_id"]
    monkeypatch.setenv("GMAIL_ALLOWED_RECIPIENTS", "owner@example.com,team@example.com")
    monkeypatch.setenv("COLLEAGUE_DAILY_SUMMARY_TO", "owner@example.com,team@example.com")

    def send(self, args, sly_data):
        del self, sly_data
        if args["to"] == "owner@example.com":
            return json.dumps({"ok": True, "sent": True, "message_id": "mail-1"})
        return json.dumps({"ok": False, "sent": False, "error": "Gmail send failed"})

    monkeypatch.setattr("coded_tools.colleague.run_finalizer.GmailSend.invoke", send)
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

    state = json.loads(state_tool.invoke({"action": "read"}, {}))["state"]
    assert result["email_summary"]["delivered"] is False
    assert result["email_summary"]["delivered_count"] == 1
    assert state["daily_email_pending"] is True
    assert state["last_email_summary_at"] is None


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


def test_malformed_optional_inputs_do_not_block_email_or_checkpoint(monkeypatch, tmp_path):
    first_run = _begin(monkeypatch, tmp_path)
    state_tool = ColleagueState()
    state_tool.invoke(
        {"action": "checkpoint", "run_id": first_run, "board_snapshot": {"digest": "a" * 64}},
        {},
    )
    state_tool.invoke({"action": "finish", "run_id": first_run}, {})
    run_id = json.loads(state_tool.invoke({"action": "begin"}, {}))["run_id"]
    monkeypatch.setenv("GMAIL_ALLOWED_RECIPIENTS", "owner@example.com")
    monkeypatch.setenv("COLLEAGUE_DAILY_SUMMARY_TO", "owner@example.com")
    monkeypatch.setattr(
        "coded_tools.colleague.run_finalizer.GmailSend.invoke",
        lambda self, args, sly_data: json.dumps({"ok": True, "sent": True, "message_id": "mail-1"}),
    )

    result = json.loads(
        RunFinalizer().invoke(
            {
                "run_id": run_id,
                "board_snapshot": {"digest": "b" * 64},
                "request_replies": "not-an-array",
                "checkpoint_ts": "30.0",
                "email_summary": {"subject": "Daily summary", "body": "Changed."},
            },
            {},
        )
    )

    state = json.loads(state_tool.invoke({"action": "read"}, {}))["state"]
    assert result["ok"] is True
    assert result["email_summary"]["delivered"] is True
    assert result["validation_warnings"] == [
        "invalid_request_replies_ignored",
        "incomplete_inbox_checkpoint_ignored",
    ]
    assert result["inbox_checkpoint"]["skipped"] is True
    assert state["board_snapshot"]["digest"] == "b" * 64
    assert state["last_email_summary_at"]
    assert state["run"] is None


def test_malformed_optional_drafts_are_ignored_and_lease_is_released(monkeypatch, tmp_path):
    run_id = _begin(monkeypatch, tmp_path)

    result = json.loads(
        RunFinalizer().invoke(
            {
                "run_id": run_id,
                "board_snapshot": "not-an-object",
                "email_summary": "not-an-object",
            },
            {},
        )
    )

    state = json.loads(ColleagueState().invoke({"action": "read"}, {}))["state"]
    assert result["ok"] is True
    assert result["email_summary"] == {"skipped": True, "reason": "agent chose no summary"}
    assert result["validation_warnings"] == [
        "invalid_board_snapshot_ignored",
        "invalid_email_summary_ignored",
    ]
    assert state["run"] is None


def test_malformed_replies_never_advance_a_valid_inbox_checkpoint(monkeypatch, tmp_path):
    run_id = _begin(monkeypatch, tmp_path)
    batch_id = create_batch(run_id, "40.0", [])

    result = json.loads(
        RunFinalizer().invoke(
            {
                "run_id": run_id,
                "request_replies": "not-an-array",
                "inbox_batch_id": batch_id,
                "checkpoint_ts": "40.0",
            },
            {},
        )
    )

    state = json.loads(ColleagueState().invoke({"action": "read"}, {}))["state"]
    assert result["ok"] is True
    assert result["inbox_checkpoint"]["skipped"] is True
    assert result["validation_warnings"] == ["invalid_request_replies_ignored"]
    assert state["last_slack_ts"] == "0"
    assert state["run"] is None
