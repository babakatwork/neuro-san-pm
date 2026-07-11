import json

from coded_tools.colleague.colleague_state import ColleagueState
from coded_tools.colleague.slack_inbox_batch import create_batch


def test_state_lease_checkpoint_and_finish(monkeypatch, tmp_path):
    state_path = tmp_path / "colleague.json"
    monkeypatch.setenv("COLLEAGUE_STATE_PATH", str(state_path))
    monkeypatch.setenv("COLLEAGUE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    tool = ColleagueState()

    first = json.loads(tool.invoke({"action": "begin"}, {}))
    assert first["acquired"] is True
    assert first["report_due"] is True
    run_id = first["run_id"]

    overlapping = json.loads(tool.invoke({"action": "begin"}, {}))
    assert overlapping["acquired"] is False

    snapshot = {"digest": "a" * 64, "item_count": 2}
    inbox_batch_id = create_batch(run_id, "123.45", [])
    checkpoint = json.loads(
        tool.invoke(
            {
                "action": "checkpoint",
                "run_id": run_id,
                "board_snapshot": snapshot,
                "last_slack_ts": "123.45",
                "inbox_batch_id": inbox_batch_id,
            },
            {},
        )
    )
    assert checkpoint["ok"] is True

    finished = json.loads(tool.invoke({"action": "finish", "run_id": run_id}, {}))
    assert finished["finished"] is True
    state = json.loads(tool.invoke({"action": "read"}, {}))["state"]
    assert state["board_snapshot"] == snapshot
    assert state["last_slack_ts"] == "123.45"
    assert state["run"] is None


def test_state_rejects_wrong_lease_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLEAGUE_STATE_PATH", str(tmp_path / "colleague.json"))
    result = json.loads(
        ColleagueState().invoke(
            {"action": "checkpoint", "run_id": "wrong", "board_snapshot": {"digest": "x"}},
            {},
        )
    )
    assert result["ok"] is False


def test_state_rejects_invalid_or_regressing_checkpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLEAGUE_STATE_PATH", str(tmp_path / "colleague.json"))
    monkeypatch.setenv("COLLEAGUE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    tool = ColleagueState()
    run_id = json.loads(tool.invoke({"action": "begin"}, {}))["run_id"]

    invalid_digest = json.loads(
        tool.invoke(
            {"action": "checkpoint", "run_id": run_id, "board_snapshot": {"digest": "short"}},
            {},
        )
    )
    assert invalid_digest["ok"] is False

    first_batch = create_batch(run_id, "20.0", [])
    first = json.loads(
        tool.invoke(
            {
                "action": "checkpoint",
                "run_id": run_id,
                "last_slack_ts": "20.0",
                "inbox_batch_id": first_batch,
            },
            {},
        )
    )
    regressing_batch = create_batch(run_id, "19.0", [])
    regressing = json.loads(
        tool.invoke(
            {
                "action": "checkpoint",
                "run_id": run_id,
                "last_slack_ts": "19.0",
                "inbox_batch_id": regressing_batch,
            },
            {},
        )
    )
    assert first["ok"] is True
    assert regressing["ok"] is False
    assert "cannot move backwards" in regressing["error"]
