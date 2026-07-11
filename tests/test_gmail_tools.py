import json
import time

from coded_tools.colleague.gmail_read import GmailRead
from coded_tools.colleague.gmail_search import GmailSearch
from coded_tools.colleague.gmail_send import GmailSend


class Call:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class Messages:
    def __init__(self):
        self.sent = []

    def list(self, **kwargs):
        self.list_args = kwargs
        return Call({"messages": [{"id": "abc", "threadId": "thread"}]})

    def get(self, **kwargs):
        self.get_args = kwargs
        return Call(
            {
                "id": kwargs["id"],
                "threadId": "thread",
                "snippet": "untrusted snippet",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Roadmap"}],
                    "mimeType": "text/plain",
                    "body": {"data": "aGVsbG8="},
                },
            }
        )

    def send(self, **kwargs):
        self.sent.append(kwargs)
        return Call({"id": "sent-1"})


class Service:
    def __init__(self):
        self.messages_api = Messages()

    def users(self):
        return self

    def messages(self):
        return self.messages_api


def test_search_applies_host_query_prefix(monkeypatch):
    service = Service()
    monkeypatch.setenv("COLLEAGUE_GMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_QUERY_PREFIX", "from:boss@example.com")
    monkeypatch.setattr("coded_tools.colleague.gmail_search.build_gmail_service", lambda scopes: service)
    result = json.loads(GmailSearch().invoke({"query": "roadmap", "limit": 99}, {}))
    assert result["ok"] is True
    assert result["count"] == 1
    assert service.messages_api.list_args["q"] == "from:boss@example.com (roadmap)"
    assert service.messages_api.list_args["maxResults"] == 20


def test_read_rejects_invalid_id_and_excludes_attachments(monkeypatch):
    assert json.loads(GmailRead().invoke({"message_id": "../../token"}, {}))["ok"] is False
    service = Service()
    monkeypatch.setenv("COLLEAGUE_GMAIL_ENABLED", "true")
    monkeypatch.setattr("coded_tools.colleague.gmail_read.build_gmail_service", lambda scopes: service)
    result = json.loads(GmailRead().invoke({"message_id": "abc"}, {}))
    assert result["body"] == "hello"
    assert "untrusted" in result["note"]


def test_send_is_allowlisted_lease_bound_and_dry_by_default(monkeypatch):
    monkeypatch.setenv("COLLEAGUE_GMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_ALLOWED_RECIPIENTS", "owner@example.com")
    monkeypatch.setattr("coded_tools.colleague.gmail_send.has_active_lease", lambda run_id: run_id == "lease")
    args = {"run_id": "lease", "to": "other@example.com", "subject": "Status", "body": "Ready"}
    assert "not in" in json.loads(GmailSend().invoke(args, {}))["error"]
    args["to"] = "owner@example.com"
    result = json.loads(GmailSend().invoke(args, {}))
    assert result["dry_run"] is True
    assert result["sent"] is False


def test_live_send_deduplicates(monkeypatch, tmp_path):
    service = Service()
    monkeypatch.setenv("COLLEAGUE_GMAIL_ENABLED", "true")
    monkeypatch.setenv("COLLEAGUE_GMAIL_WRITE_ENABLED", "true")
    monkeypatch.setenv("GMAIL_ALLOWED_RECIPIENTS", "owner@example.com")
    monkeypatch.setenv("COLLEAGUE_STATE_PATH", str(tmp_path / "colleague.json"))
    monkeypatch.setattr("coded_tools.colleague.gmail_send.has_active_lease", lambda run_id: True)
    monkeypatch.setattr("coded_tools.colleague.gmail_send.build_gmail_service", lambda scopes: service)
    args = {"run_id": "lease", "to": "owner@example.com", "subject": "Status", "body": "Ready"}
    assert json.loads(GmailSend().invoke(args, {}))["sent"] is True
    assert json.loads(GmailSend().invoke(args, {}))["duplicate"] is True
    assert len(service.messages_api.sent) == 1
    assert time.time() - json.loads((tmp_path / "gmail_delivery.json").read_text())["sent"].popitem()[1] < 2
