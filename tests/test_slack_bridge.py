from apps.slack_bridge import build_event_payload
from apps.slack_bridge import claim_event
from apps.slack_bridge import is_allowed_event
from apps.slack_bridge import release_event


def test_bridge_allowlist_and_wake_only_payload(monkeypatch):
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U1")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "B1")
    event = {
        "channel": "C1",
        "user": "U1",
        "text": "<@B1> private product request",
        "ts": "12.34",
    }

    assert is_allowed_event(event) is True
    payload = build_event_payload(event)
    assert payload["chat_filter"] == {"chat_filter_type": "MINIMAL"}
    assert payload["sly_data"]["slack_thread_ts"] == "12.34"
    assert "TRUSTED_SLACK_WAKE" in payload["user_message"]["text"]
    assert "private product request" not in payload["user_message"]["text"]


def test_bridge_rejects_other_users(monkeypatch):
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U1")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "B1")
    assert is_allowed_event({"channel": "C1", "user": "U2", "text": "hi"}) is False


def test_bridge_event_dedupe_is_durable_and_releasable(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLEAGUE_STATE_PATH", str(tmp_path / "colleague.json"))
    event = {"channel": "C1", "user": "U1", "ts": "12.34"}

    assert claim_event("Ev123", event) is True
    assert claim_event("Ev123", event) is False
    release_event("Ev123")
    assert claim_event("Ev123", event) is True
    assert claim_event("", event) is False
