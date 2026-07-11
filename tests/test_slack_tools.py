import json

from coded_tools.colleague._slack_client import SlackApiClient
from coded_tools.colleague._slack_client import SlackApiError
from coded_tools.colleague.colleague_state import ColleagueState
from coded_tools.colleague.slack_event_queue import claim_event
from coded_tools.colleague.slack_event_queue import dead_letters
from coded_tools.colleague.slack_event_queue import pending_events
from coded_tools.colleague.slack_inbox import SlackInbox
from coded_tools.colleague.slack_inbox_batch import create_batch
from coded_tools.colleague.slack_post import SlackPost


def set_slack_config(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "B123")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U1")
    monkeypatch.setenv("COLLEAGUE_SLACK_REQUIRE_MENTION", "true")
    monkeypatch.setenv("COLLEAGUE_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("COLLEAGUE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))


def begin_run():
    return json.loads(ColleagueState().invoke({"action": "begin"}, {}))["run_id"]


def test_slack_inbox_paginates_filters_mentions_and_returns_scanned_high_water(monkeypatch, tmp_path):
    set_slack_config(monkeypatch, tmp_path)
    run_id = begin_run()
    calls = []

    def fake_call(self, method, *, http_method, payload):
        del self
        calls.append(payload)
        assert method == "conversations.history"
        assert http_method == "GET"
        assert payload["channel"] == "C123"
        if "cursor" not in payload:
            return {
                "ok": True,
                "messages": [
                    {"user": "U2", "text": "not allowed", "ts": "6.0"},
                    {"user": "U1", "text": "<@B123> second", "ts": "5.0"},
                ],
                "has_more": True,
                "response_metadata": {"next_cursor": "page-two"},
            }
        return {
            "ok": True,
            "messages": [
                {"user": "U1", "text": "ambient conversation", "ts": "3.0"},
                {"user": "U1", "text": "<@B123> first", "ts": "2.0"},
            ],
            "response_metadata": {"next_cursor": ""},
        }

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    result = json.loads(SlackInbox().invoke({"oldest": "1.0", "run_id": run_id}, {}))

    assert [message["text"] for message in result["messages"]] == ["first", "second"]
    assert float(result["checkpoint_ts"]) > 6.0
    assert result["scanned_count"] == 4
    assert result["complete"] is True
    assert calls[1]["cursor"] == "page-two"


def test_slack_inbox_does_not_checkpoint_an_overflowing_backlog(monkeypatch, tmp_path):
    set_slack_config(monkeypatch, tmp_path)
    run_id = begin_run()
    monkeypatch.setenv("COLLEAGUE_SLACK_MAX_PAGES", "1")

    def fake_call(self, method, *, http_method, payload):
        del self, method, http_method, payload
        return {
            "ok": True,
            "messages": [{"user": "U1", "text": "<@B123> request", "ts": "2.0"}],
            "has_more": True,
            "response_metadata": {"next_cursor": "still-more"},
        }

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    result = json.loads(SlackInbox().invoke({"oldest": "1.0", "run_id": run_id}, {}))

    assert result["ok"] is False
    assert "checkpoint" in result["error"]
    assert "checkpoint_ts" not in result


def test_slack_inbox_bootstraps_from_a_bounded_lookback(monkeypatch, tmp_path):
    set_slack_config(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLEAGUE_SLACK_INITIAL_LOOKBACK_HOURS", "24")
    run_id = begin_run()
    calls = []

    def fake_call(self, method, *, http_method, payload):
        del self, method, http_method
        calls.append(payload)
        return {"ok": True, "messages": [], "response_metadata": {"next_cursor": ""}}

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    result = json.loads(SlackInbox().invoke({"oldest": "0", "run_id": run_id}, {}))

    assert result["ok"] is True
    assert result["bootstrap"] is True
    assert result["checkpoint_ts"] == calls[0]["latest"]
    assert 86_399 <= float(calls[0]["latest"]) - float(calls[0]["oldest"]) <= 86_401


def test_slack_inbox_rejects_invalid_http_timeout(monkeypatch, tmp_path):
    set_slack_config(monkeypatch, tmp_path)
    run_id = begin_run()
    monkeypatch.setenv("SLACK_HTTP_TIMEOUT_SECONDS", "soon")

    result = json.loads(SlackInbox().invoke({"oldest": "0", "run_id": run_id}, {}))

    assert result["ok"] is False
    assert "must be numeric" in result["error"]


def test_thread_event_survives_in_queue_until_state_checkpoint(monkeypatch, tmp_path):
    set_slack_config(monkeypatch, tmp_path)
    event = {
        "channel": "C123",
        "user": "U1",
        "text": "<@B123> thread follow-up",
        "ts": "12.0",
        "thread_ts": "10.0",
    }
    assert claim_event("EvThread", event) is True
    run_id = begin_run()

    def fake_call(self, method, *, http_method, payload):
        del self, http_method
        if method == "conversations.history":
            return {"ok": True, "messages": [], "response_metadata": {"next_cursor": ""}}
        if method == "conversations.replies":
            assert payload["ts"] == "10.0"
            return {
                "ok": True,
                "messages": [event],
                "response_metadata": {"next_cursor": ""},
            }
        assert method == "chat.postMessage"
        assert payload["thread_ts"] == "10.0"
        return {"ok": True, "ts": "13.0"}

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    inbox = json.loads(SlackInbox().invoke({"oldest": "11.0", "run_id": run_id}, {}))

    assert inbox["messages"][0]["text"] == "thread follow-up"
    assert pending_events("C123", {"U1"})

    monkeypatch.setenv("COLLEAGUE_SLACK_WRITE_ENABLED", "true")
    delivered = json.loads(
        SlackPost().invoke(
            {
                "text": "Here is the answer.",
                "run_id": run_id,
                "inbox_batch_id": inbox["inbox_batch_id"],
                "reply_to_ts": "12.0",
            },
            {},
        )
    )
    assert delivered["sent"] is True
    checkpoint = json.loads(
        ColleagueState().invoke(
            {
                "action": "checkpoint",
                "run_id": run_id,
                "last_slack_ts": inbox["checkpoint_ts"],
                "inbox_batch_id": inbox["inbox_batch_id"],
            },
            {},
        )
    )
    assert checkpoint["ok"] is True
    assert checkpoint["completed_event_count"] == 1
    assert pending_events("C123", {"U1"}) == []
    assert claim_event("EvThread", event) is False


def test_unresolvable_event_is_quarantined_after_bounded_retries(monkeypatch, tmp_path):
    set_slack_config(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLEAGUE_SLACK_EVENT_MAX_ATTEMPTS", "2")
    event = {
        "channel": "C123",
        "user": "U1",
        "text": "<@B123> deleted request",
        "ts": "12.0",
        "thread_ts": "10.0",
    }
    assert claim_event("EvDeleted", event) is True

    def fake_call(self, method, *, http_method, payload):
        del self, http_method, payload
        if method == "conversations.replies":
            raise SlackApiError("Slack API rejected the request: thread_not_found", code="thread_not_found")
        return {"ok": True, "messages": [], "response_metadata": {"next_cursor": ""}}

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    first_run = begin_run()
    first = json.loads(SlackInbox().invoke({"oldest": "1.0", "run_id": first_run}, {}))
    ColleagueState().invoke({"action": "finish", "run_id": first_run}, {})
    second_run = begin_run()
    second = json.loads(SlackInbox().invoke({"oldest": "1.0", "run_id": second_run}, {}))

    assert first["ok"] is False
    assert second["ok"] is True
    assert "EvDeleted" in dead_letters()
    assert pending_events("C123", {"U1"}) == []


def test_event_outside_a_changed_allowlist_is_quarantined(monkeypatch, tmp_path):
    set_slack_config(monkeypatch, tmp_path)
    event = {
        "channel": "C123",
        "user": "U1",
        "text": "<@B123> old teammate request",
        "ts": "12.0",
    }
    assert claim_event("EvFormerUser", event) is True
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U2")
    run_id = begin_run()

    def fake_call(self, method, *, http_method, payload):
        del self, method, http_method, payload
        return {"ok": True, "messages": [], "response_metadata": {"next_cursor": ""}}

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    result = json.loads(SlackInbox().invoke({"oldest": "1.0", "run_id": run_id}, {}))

    assert result["ok"] is True
    assert dead_letters()["EvFormerUser"]["reason"] == "boundary_changed"


def test_slack_post_is_dry_run_by_default(monkeypatch, tmp_path):
    set_slack_config(monkeypatch, tmp_path)
    monkeypatch.delenv("COLLEAGUE_SLACK_WRITE_ENABLED", raising=False)
    run_id = begin_run()

    result = json.loads(SlackPost().invoke({"text": "Board is healthy.", "run_id": run_id}, {}))

    assert result["ok"] is True
    assert result["sent"] is False
    assert result["dry_run"] is True
    assert "Board is healthy." in result["preview"]


def test_slack_post_uses_fixed_channel_plain_text_and_deduplicates(monkeypatch, tmp_path):
    set_slack_config(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLEAGUE_SLACK_WRITE_ENABLED", "true")
    run_id = begin_run()
    calls = []

    def fake_call(self, method, *, http_method, payload):
        del self
        calls.append((method, http_method, payload))
        return {"ok": True, "ts": "20.0"}

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    untrusted_sly_data = {"slack_channel_id": "C123", "slack_thread_ts": "10.0"}
    text = "A useful update <!channel> <@U9> <https://attacker.invalid>"
    tool = SlackPost()
    first = json.loads(tool.invoke({"text": text, "run_id": run_id}, untrusted_sly_data))
    second = json.loads(tool.invoke({"text": text, "run_id": run_id}, untrusted_sly_data))

    assert first["sent"] is True
    assert second["duplicate"] is True
    assert len(calls) == 1
    payload = calls[0][2]
    assert payload["channel"] == "C123"
    assert "thread_ts" not in payload
    assert payload["mrkdwn"] is False
    assert payload["unfurl_links"] is False
    assert payload["unfurl_media"] is False
    assert "<!channel>" not in payload["text"]
    assert "<@U9>" not in payload["text"]


def test_slack_post_rejects_missing_run_lease(monkeypatch, tmp_path):
    set_slack_config(monkeypatch, tmp_path)
    result = json.loads(SlackPost().invoke({"text": "should not send", "run_id": "missing"}, {}))
    assert result["ok"] is False
    assert "active colleague lease" in result["error"]


def test_dry_run_cannot_consume_an_unanswered_inbox_batch(monkeypatch, tmp_path):
    set_slack_config(monkeypatch, tmp_path)
    run_id = begin_run()
    batch_id = create_batch(
        run_id,
        "20.0",
        [{"ts": "10.0", "thread_ts": "10.0", "event_ids": []}],
    )

    preview = json.loads(
        SlackPost().invoke(
            {
                "text": "Preview only",
                "run_id": run_id,
                "inbox_batch_id": batch_id,
                "reply_to_ts": "10.0",
            },
            {},
        )
    )
    checkpoint = json.loads(
        ColleagueState().invoke(
            {
                "action": "checkpoint",
                "run_id": run_id,
                "last_slack_ts": "20.0",
                "inbox_batch_id": batch_id,
            },
            {},
        )
    )

    assert preview["dry_run"] is True
    assert checkpoint["ok"] is False
    assert "must be delivered" in checkpoint["error"]
