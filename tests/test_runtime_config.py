import json

import pytest

from coded_tools.colleague._runtime import env_bool
from coded_tools.colleague._runtime import read_env_bool
from coded_tools.colleague.runtime_config import RuntimeConfig


def set_valid_config(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret-value")
    monkeypatch.setenv("GITHUB_PROJECT_OWNER", "cognizant-ai-lab")
    monkeypatch.setenv("GITHUB_PROJECT_OWNER_TYPE", "org")
    monkeypatch.setenv("GITHUB_PROJECT_NUMBER", "7")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "slack-secret-value")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "B123")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U1,U2")
    monkeypatch.setenv("COLLEAGUE_SLACK_WRITE_ENABLED", "false")
    monkeypatch.setenv("COLLEAGUE_SLACK_REQUIRE_MENTION", "true")
    monkeypatch.setenv("COLLEAGUE_MAX_RUN_SECONDS", "600")
    monkeypatch.setenv("COLLEAGUE_REPORT_INTERVAL_HOURS", "24")
    monkeypatch.setenv("COLLEAGUE_STALE_AFTER_DAYS", "14")
    monkeypatch.setenv("COLLEAGUE_MAX_PROJECT_ITEMS", "500")
    monkeypatch.setenv("COLLEAGUE_SLACK_MAX_PAGES", "10")
    monkeypatch.setenv("COLLEAGUE_SLACK_MAX_REQUESTS", "50")
    monkeypatch.setenv("COLLEAGUE_SLACK_MAX_THREAD_PAGES", "10")
    monkeypatch.setenv("COLLEAGUE_SLACK_INITIAL_LOOKBACK_HOURS", "24")
    monkeypatch.setenv("COLLEAGUE_SLACK_EVENT_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("AGENT_REQUEST_LOGGING_INPUT_SLICE", "0")


def test_runtime_config_never_returns_secrets(monkeypatch):
    set_valid_config(monkeypatch)

    raw = RuntimeConfig().invoke({}, {})
    result = json.loads(raw)

    assert result["ok"] is True
    assert result["github"]["project_number"] == 7
    assert result["slack"]["allowed_user_count"] == 2
    assert result["slack"]["require_mention"] is True
    assert "github-secret-value" not in raw
    assert "slack-secret-value" not in raw


@pytest.mark.parametrize("value", ["not-a-number", "0", "-1"])
def test_runtime_config_reports_invalid_project_number(monkeypatch, value):
    set_valid_config(monkeypatch)
    monkeypatch.setenv("GITHUB_PROJECT_NUMBER", value)

    result = json.loads(RuntimeConfig().invoke({}, {}))

    assert result["ok"] is False
    assert "GITHUB_PROJECT_NUMBER must be a positive integer" in result["missing"]


def test_runtime_config_includes_missing_slack_readiness(monkeypatch):
    set_valid_config(monkeypatch)
    monkeypatch.delenv("SLACK_BOT_USER_ID")

    result = json.loads(RuntimeConfig().invoke({}, {}))

    assert result["ok"] is False
    assert result["slack"]["read_ready"] is False
    assert "SLACK_BOT_USER_ID" in result["missing"]


@pytest.mark.parametrize(
    ("name", "value", "field", "safe_value"),
    [
        ("COLLEAGUE_SLACK_WRITE_ENABLED", "flase", "write_enabled", False),
        ("COLLEAGUE_SLACK_REQUIRE_MENTION", "tru", "require_mention", True),
    ],
)
def test_runtime_config_surfaces_invalid_booleans(monkeypatch, name, value, field, safe_value):
    set_valid_config(monkeypatch)
    monkeypatch.setenv(name, value)

    result = json.loads(RuntimeConfig().invoke({}, {}))

    assert result["ok"] is False
    assert result["slack"][field] is safe_value
    assert f"{name} must be a recognized boolean (true/false)" in result["missing"]
    if name == "COLLEAGUE_SLACK_REQUIRE_MENTION":
        assert result["slack"]["read_ready"] is False


@pytest.mark.parametrize(
    "name",
    [
        "COLLEAGUE_MAX_RUN_SECONDS",
        "COLLEAGUE_REPORT_INTERVAL_HOURS",
        "COLLEAGUE_STALE_AFTER_DAYS",
        "COLLEAGUE_MAX_PROJECT_ITEMS",
        "COLLEAGUE_SLACK_MAX_PAGES",
        "COLLEAGUE_SLACK_MAX_REQUESTS",
        "COLLEAGUE_SLACK_MAX_THREAD_PAGES",
        "COLLEAGUE_SLACK_INITIAL_LOOKBACK_HOURS",
        "COLLEAGUE_SLACK_EVENT_MAX_ATTEMPTS",
    ],
)
def test_runtime_config_requires_positive_policy_integers(monkeypatch, name):
    set_valid_config(monkeypatch)
    monkeypatch.setenv(name, "0")

    result = json.loads(RuntimeConfig().invoke({}, {}))

    assert result["ok"] is False
    assert f"{name} must be a positive integer" in result["missing"]


@pytest.mark.parametrize("value", ["true", "YES", "1", "on"])
def test_strict_env_bool_accepts_known_true_values(monkeypatch, value):
    monkeypatch.setenv("TEST_BOOLEAN", value)
    assert read_env_bool("TEST_BOOLEAN") == (True, None)


@pytest.mark.parametrize("value", ["false", "NO", "0", "off"])
def test_strict_env_bool_accepts_known_false_values(monkeypatch, value):
    monkeypatch.setenv("TEST_BOOLEAN", value)
    assert read_env_bool("TEST_BOOLEAN", default=True) == (False, None)


@pytest.mark.parametrize("invalid_value", ["flase", ""])
def test_strict_env_bool_fails_closed_on_invalid_value(monkeypatch, invalid_value):
    monkeypatch.setenv("TEST_BOOLEAN", invalid_value)

    value, error = read_env_bool("TEST_BOOLEAN", default=True)

    assert value is True
    assert error == "TEST_BOOLEAN must be a recognized boolean (true/false)"
    assert env_bool("TEST_BOOLEAN", default=True) is True
    assert env_bool("TEST_BOOLEAN", default=False) is False


def test_runtime_config_requires_timeout_to_match_registry(monkeypatch):
    set_valid_config(monkeypatch)
    monkeypatch.setenv("COLLEAGUE_MAX_RUN_SECONDS", "601")

    result = json.loads(RuntimeConfig().invoke({}, {}))

    assert result["ok"] is False
    assert "COLLEAGUE_MAX_RUN_SECONDS must be 600 to match the agent timeout" in result["missing"]


def test_runtime_config_requires_request_text_redaction(monkeypatch):
    set_valid_config(monkeypatch)
    monkeypatch.delenv("AGENT_REQUEST_LOGGING_INPUT_SLICE")

    result = json.loads(RuntimeConfig().invoke({}, {}))

    assert result["ok"] is False
    assert "AGENT_REQUEST_LOGGING_INPUT_SLICE must be 0" in result["missing"]
