from scripts.slack_availability import main
from scripts.slack_availability import set_availability

from coded_tools.colleague._slack_client import SlackApiClient
from coded_tools.colleague._slack_client import SlackApiError


def configure(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")
    monkeypatch.setenv("COLLEAGUE_SLACK_WRITE_ENABLED", "true")
    monkeypatch.setenv("COLLEAGUE_SLACK_AVAILABILITY_ENABLED", "true")
    monkeypatch.setenv("COLLEAGUE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))


def test_availability_posts_fixed_plain_text_notice(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = []

    def fake_call(self, method, *, http_method, payload):
        del self
        calls.append((method, http_method, payload))
        return {"ok": True, "ts": "20.0"}

    monkeypatch.setattr(SlackApiClient, "call", fake_call)

    assert set_availability("offline") is True
    assert calls == [
        (
            "chat.postMessage",
            "POST",
            {
                "channel": "C123",
                "text": "[neuro-san colleague] Colleague is offline and will not respond until it is restarted.",
                "mrkdwn": False,
                "unfurl_links": False,
                "unfurl_media": False,
            },
        )
    ]


def test_availability_failure_is_best_effort(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)

    def fake_call(self, method, *, http_method, payload):
        del self, method, http_method, payload
        raise SlackApiError("Slack API request failed")

    monkeypatch.setattr(SlackApiClient, "call", fake_call)

    assert set_availability("online") is False
    assert main(["online"]) == 0


def test_availability_does_not_post_when_writes_are_disabled(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLEAGUE_SLACK_WRITE_ENABLED", "false")

    def unexpected_call(*args, **kwargs):
        del args, kwargs
        raise AssertionError("Slack should not be called")

    monkeypatch.setattr(SlackApiClient, "call", unexpected_call)

    assert set_availability("offline") is True


def test_availability_does_not_post_when_notices_are_disabled(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLEAGUE_SLACK_AVAILABILITY_ENABLED", "false")

    def unexpected_call(*args, **kwargs):
        del args, kwargs
        raise AssertionError("Slack should not be called")

    monkeypatch.setattr(SlackApiClient, "call", unexpected_call)

    assert set_availability("online") is True
