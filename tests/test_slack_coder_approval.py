import hashlib
import json

from coded_tools.colleague._slack_client import SlackApiClient
from coded_tools.colleague.slack_coder_approval import SlackCoderApproval

ISSUE_URL = "https://github.com/cognizant-ai-lab/neuro-san/issues/123"


def configure(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U1,U2")
    monkeypatch.setenv("COLLEAGUE_SLACK_WRITE_ENABLED", "true")
    monkeypatch.setenv("AGENTIC_DELIVERY_STATE_PATH", str(tmp_path / "delivery.json"))
    monkeypatch.setenv("COLLEAGUE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))


def propose(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    posted = []

    def fake_call(self, method, *, http_method, payload):
        del self, http_method
        assert method == "chat.postMessage"
        posted.append(payload)
        return {"ok": True, "ts": "100.1"}

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    result = json.loads(
        SlackCoderApproval().invoke(
            {"action": "propose", "issue_url": ISSUE_URL, "reason": "Small change with explicit acceptance tests."},
            {},
        )
    )
    return result, posted


def thread_payload(*messages):
    return {
        "ok": True,
        "messages": [
            {"user": "B1", "bot_id": "B1", "text": "proposal", "ts": "100.1"},
            *messages,
        ],
        "response_metadata": {"next_cursor": ""},
    }


def test_proposal_is_host_templated_persisted_and_idempotent(monkeypatch, tmp_path):
    first, posted = propose(monkeypatch, tmp_path)

    def must_not_post(self, method, *, http_method, payload):
        del self, http_method, payload
        if method == "conversations.replies":
            return thread_payload()
        raise AssertionError("duplicate proposal must not post")

    monkeypatch.setattr(SlackApiClient, "call", must_not_post)
    second = json.loads(
        SlackCoderApproval().invoke(
            {"action": "propose", "issue_url": ISSUE_URL, "reason": "A different model-generated reason."}, {}
        )
    )

    assert first["created"] is True
    assert first["state"] == "pending"
    assert second["duplicate"] is True
    assert second["proposal_id"] == first["proposal_id"]
    assert len(posted) == 1
    assert "Reply naturally" in posted[0]["text"]
    raw_state = (tmp_path / "delivery.json").read_text()
    assert "Small change" not in raw_state


def test_replies_expose_only_bounded_allowlisted_exact_thread_human_text(monkeypatch, tmp_path):
    proposal, _posted = propose(monkeypatch, tmp_path)

    def fake_call(self, method, *, http_method, payload):
        del self, http_method
        assert method == "conversations.replies"
        assert payload["channel"] == "C123"
        assert payload["ts"] == "100.1"
        return thread_payload(
            {"user": "U9", "text": "go ahead", "ts": "101.1", "thread_ts": "100.1"},
            {"user": "U1", "text": "wrong thread", "ts": "101.2", "thread_ts": "99.1"},
            {"user": "U1", "bot_id": "B2", "text": "approved", "ts": "101.3", "thread_ts": "100.1"},
            {"user": "U1", "subtype": "bot_message", "text": "yes", "ts": "101.4", "thread_ts": "100.1"},
            {
                "user": "U1",
                "text": "Looks good—please have the coder take it.",
                "ts": "101.6",
                "thread_ts": "100.1",
            },
            {"user": "U2", "text": "I would rather we skip this one.", "ts": "101.5", "thread_ts": "100.1"},
            {"user": "U1", "text": "x" * 2001, "ts": "101.7", "thread_ts": "100.1"},
        )

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    result = json.loads(
        SlackCoderApproval().invoke({"action": "replies", "proposal_id": proposal["proposal_id"]}, {})
    )

    assert result["state"] == "pending"
    assert result["replies"] == [
        {"ts": "101.5", "user": "U2", "text": "I would rather we skip this one.", "decision_hint": "unclear"},
        {"ts": "101.6", "user": "U1", "text": "Looks good—please have the coder take it.", "decision_hint": "unclear"},
    ]
    assert result["decision_hints"] == {"101.5": "unclear", "101.6": "unclear"}
    assert "untrusted" in result["content_trust"]


def test_agent_decision_is_bound_to_refetched_verified_message(monkeypatch, tmp_path):
    proposal, _posted = propose(monkeypatch, tmp_path)
    reply_text = "Looks good to me—please have the coder take it and keep the PR small."

    def fake_call(self, method, *, http_method, payload):
        del self, http_method, payload
        assert method == "conversations.replies"
        return thread_payload({"user": "U2", "text": reply_text, "ts": "101.5", "thread_ts": "100.1"})

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    result = json.loads(
        SlackCoderApproval().invoke(
            {
                "action": "decide",
                "proposal_id": proposal["proposal_id"],
                "message_ts": "101.5",
                "decision": "approve",
            },
            {},
        )
    )

    assert result["state"] == "approved"
    assert result["decided_by"] == "U2"
    assert result["decision_ts"] == "101.5"
    raw_state = json.loads((tmp_path / "delivery.json").read_text())
    record = raw_state["proposals"][proposal["proposal_id"]]
    assert record["decision_source"] == "agent_interpreted_slack_reply"
    assert record["decision_message_sha256"] == hashlib.sha256(reply_text.encode()).hexdigest()
    assert reply_text not in (tmp_path / "delivery.json").read_text()



def test_common_natural_language_replies_get_safe_host_hints(monkeypatch, tmp_path):
    proposal, _posted = propose(monkeypatch, tmp_path)

    def fake_call(self, method, *, http_method, payload):
        del self, http_method, payload
        assert method == "conversations.replies"
        return thread_payload(
            {"user": "U1", "text": "Approved", "ts": "101.1", "thread_ts": "100.1"},
            {"user": "U2", "text": "yes, go ahead", "ts": "101.2", "thread_ts": "100.1"},
            {"user": "U1", "text": "no, skip it", "ts": "101.3", "thread_ts": "100.1"},
            {"user": "U2", "text": "Maybe after we clarify scope", "ts": "101.4", "thread_ts": "100.1"},
        )

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    result = json.loads(SlackCoderApproval().invoke({"action": "replies", "proposal_id": proposal["proposal_id"]}, {}))
    assert result["decision_hints"] == {"101.1": "approve", "101.2": "approve", "101.3": "reject", "101.4": "unclear"}


def test_replies_host_commits_one_clear_natural_language_decision(monkeypatch, tmp_path):
    proposal, _posted = propose(monkeypatch, tmp_path)

    def fake_call(self, method, *, http_method, payload):
        del self, http_method, payload
        assert method == "conversations.replies"
        return thread_payload(
            {"user": "U1", "text": "Approved", "ts": "101.1", "thread_ts": "100.1"},
        )

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    result = json.loads(SlackCoderApproval().invoke({"action": "replies", "proposal_id": proposal["proposal_id"]}, {}))
    assert result["state"] == "approved"
    assert result["decision_ts"] == "101.1"
    assert result["decided_by"] == "U1"
    state = json.loads((tmp_path / "delivery.json").read_text())
    assert state["proposals"][proposal["proposal_id"]]["decision_source"] == "host_classified_slack_reply"

def test_rejection_is_terminal_and_repeated_decision_does_not_refetch(monkeypatch, tmp_path):
    proposal, _posted = propose(monkeypatch, tmp_path)
    calls = 0

    def fake_call(self, method, *, http_method, payload):
        nonlocal calls
        del self, method, http_method, payload
        calls += 1
        return thread_payload(
            {"user": "U1", "text": "I'd rather not assign this one right now.", "ts": "102.1", "thread_ts": "100.1"}
        )

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    args = {
        "action": "decide",
        "proposal_id": proposal["proposal_id"],
        "message_ts": "102.1",
        "decision": "reject",
    }
    first = json.loads(SlackCoderApproval().invoke(args, {}))
    second = json.loads(SlackCoderApproval().invoke(args, {}))

    assert first["state"] == "rejected"
    assert second["state"] == "rejected"
    assert second["duplicate"] is True
    assert calls == 1


def test_fabricated_or_ineligible_message_timestamp_cannot_authorize(monkeypatch, tmp_path):
    proposal, _posted = propose(monkeypatch, tmp_path)

    def fake_call(self, method, *, http_method, payload):
        del self, method, http_method, payload
        return thread_payload(
            {"user": "U9", "text": "yes", "ts": "101.1", "thread_ts": "100.1"},
            {"user": "U1", "text": "yes", "ts": "101.2", "thread_ts": "99.1"},
            {"user": "U1", "bot_id": "B2", "text": "yes", "ts": "101.3", "thread_ts": "100.1"},
        )

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    result = json.loads(
        SlackCoderApproval().invoke(
            {
                "action": "decide",
                "proposal_id": proposal["proposal_id"],
                "message_ts": "101.3",
                "decision": "approve",
            },
            {},
        )
    )
    assert result["ok"] is False
    state = json.loads((tmp_path / "delivery.json").read_text())
    assert state["proposals"][proposal["proposal_id"]]["state"] == "pending"


def test_clarification_is_host_templated_thread_bound_and_deduplicated(monkeypatch, tmp_path):
    proposal, _posted = propose(monkeypatch, tmp_path)
    calls = []

    def fake_call(self, method, *, http_method, payload):
        del self, http_method
        calls.append((method, payload))
        if method == "conversations.replies":
            return thread_payload(
                {"user": "U1", "text": "Maybe, what do you think?", "ts": "103.1", "thread_ts": "100.1"}
            )
        assert method == "chat.postMessage"
        return {"ok": True, "ts": "103.2"}

    monkeypatch.setattr(SlackApiClient, "call", fake_call)
    args = {
        "action": "clarify",
        "proposal_id": proposal["proposal_id"],
        "message_ts": "103.1",
        "clarification": "I want to make sure I understood you correctly.",
    }
    first = json.loads(SlackCoderApproval().invoke(args, {}))
    second = json.loads(SlackCoderApproval().invoke(args, {}))

    assert first["sent"] is True
    assert second["duplicate"] is True
    assert [method for method, _payload in calls] == ["conversations.replies", "chat.postMessage"]
    clarification = calls[1][1]
    assert clarification["channel"] == "C123"
    assert clarification["thread_ts"] == "100.1"
    assert "I want to make sure" in clarification["text"]
    assert "To confirm: should I assign issue #123" in clarification["text"]
    assert "Maybe" not in (tmp_path / "delivery.json").read_text()


def test_expired_proposal_fails_closed_without_slack_lookup(monkeypatch, tmp_path):
    proposal, _posted = propose(monkeypatch, tmp_path)
    path = tmp_path / "delivery.json"
    state = json.loads(path.read_text())
    state["proposals"][proposal["proposal_id"]]["expires_at"] = 0
    path.write_text(json.dumps(state))

    def must_not_call(*args, **kwargs):
        raise AssertionError("expired proposal must not query Slack")

    monkeypatch.setattr(SlackApiClient, "call", must_not_call)
    result = json.loads(
        SlackCoderApproval().invoke({"action": "replies", "proposal_id": proposal["proposal_id"]}, {})
    )
    assert result["state"] == "expired"
    assert result["replies"] == []


def test_dry_run_does_not_create_approvable_state(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLEAGUE_SLACK_WRITE_ENABLED", "false")
    result = json.loads(
        SlackCoderApproval().invoke(
            {"action": "propose", "issue_url": ISSUE_URL, "reason": "Suitable test ticket."}, {}
        )
    )
    assert result["dry_run"] is True
    assert result["created"] is False
    assert not (tmp_path / "delivery.json").exists()
