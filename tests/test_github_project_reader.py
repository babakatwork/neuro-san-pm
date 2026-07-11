import json

import requests

from coded_tools.colleague.github_project_reader import GRAPHQL_URL
from coded_tools.colleague.github_project_reader import GitHubProjectReader


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.body = body
        self.status_code = status_code

    def json(self):
        return self.body


def set_github_config(monkeypatch, tmp_path, *, owner_type="org", max_items="500"):
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret-token")
    monkeypatch.setenv("GITHUB_PROJECT_OWNER", "cognizant-ai-lab")
    monkeypatch.setenv("GITHUB_PROJECT_OWNER_TYPE", owner_type)
    monkeypatch.setenv("GITHUB_PROJECT_NUMBER", "7")
    monkeypatch.setenv("COLLEAGUE_MAX_PROJECT_ITEMS", max_items)
    monkeypatch.setenv("GITHUB_HTTP_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("COLLEAGUE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))


def project_page(nodes, *, has_next=False, cursor=None, owner_field="organization"):
    return {
        "data": {
            owner_field: {
                "projectV2": {
                    "id": "PVT_configured",
                    "number": 7,
                    "title": "Neuro SAN delivery",
                    "url": "https://github.com/orgs/cognizant-ai-lab/projects/7",
                    "items": {
                        "nodes": nodes,
                        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    },
                }
            }
        }
    }


def issue_node():
    return {
        "id": "PVTI_issue",
        "updatedAt": "2026-07-10T12:00:00Z",
        "status": {"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": "In progress"},
        "priority": {"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": "P1"},
        "content": {
            "__typename": "Issue",
            "id": "I_issue",
            "number": 839,
            "title": "Event invocation",
            "url": "https://github.com/cognizant-ai-lab/neuro-san/issues/839",
            "updatedAt": "2026-07-11T09:30:00Z",
            "assignees": {
                "totalCount": 2,
                "nodes": [{"login": "zoe"}, {"login": "amy"}],
            },
            "labels": {
                "totalCount": 2,
                "nodes": [{"name": "runtime"}, {"name": "priority"}],
            },
        },
    }


def draft_node():
    return {
        "id": "PVTI_draft",
        "updatedAt": "2026-07-11T10:00:00Z",
        "status": None,
        "priority": None,
        "content": {
            "__typename": "DraftIssue",
            "id": "DI_draft",
            "title": "Plan the next milestone",
            "updatedAt": "2026-07-11T10:00:00Z",
            "assignees": {"totalCount": 1, "nodes": [{"login": "owner"}]},
        },
    }


def test_reader_uses_only_fixed_env_and_paginates_normalized_items(monkeypatch, tmp_path):
    set_github_config(monkeypatch, tmp_path)
    responses = [
        FakeResponse(project_page([issue_node()], has_next=True, cursor="cursor-two")),
        FakeResponse(project_page([draft_node()])),
    ]
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(requests, "post", fake_post)
    result = json.loads(
        GitHubProjectReader().invoke(
            {"owner": "attacker", "project_number": 999, "repository": "private-repo"},
            {"owner": "also-attacker"},
        )
    )

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["project"] == {
        "number": 7,
        "owner": "cognizant-ai-lab",
        "owner_type": "org",
        "title": "Neuro SAN delivery",
        "url": "https://github.com/orgs/cognizant-ai-lab/projects/7",
    }
    assert result["item_count"] == 2
    assert result["items"][0] == {
        "assignees": ["amy", "zoe"],
        "id": "PVTI_issue",
        "labels": ["priority", "runtime"],
        "number": "839",
        "priority": "P1",
        "status": "In progress",
        "title": "Event invocation",
        "type": "Issue",
        "updated_at": "2026-07-11T09:30:00Z",
        "url": "https://github.com/cognizant-ai-lab/neuro-san/issues/839",
    }
    assert result["items"][1]["status"] == "No status"
    assert result["items"][1]["assignees"] == ["owner"]
    assert result["items"][1]["url"] == ""

    assert len(calls) == 2
    for url, kwargs in calls:
        payload = kwargs["json"]
        assert url == GRAPHQL_URL
        assert payload["operationName"] == "ReadConfiguredProject"
        assert "mutation" not in payload["query"].lower()
        assert "organization(login: $owner)" in payload["query"]
        assert "user(login: $owner)" not in payload["query"]
        assert payload["variables"]["owner"] == "cognizant-ai-lab"
        assert payload["variables"]["number"] == 7
        assert "attacker" not in json.dumps(payload)
        assert kwargs["headers"]["Authorization"] == "Bearer github-secret-token"
        assert kwargs["timeout"] == (5.0, 12.0)
        assert kwargs["allow_redirects"] is False
    assert calls[0][1]["json"]["variables"]["cursor"] is None
    assert calls[1][1]["json"]["variables"]["cursor"] == "cursor-two"
    assert "github-secret-token" not in json.dumps(result)


def test_reader_uses_fixed_user_owner_query(monkeypatch, tmp_path):
    set_github_config(monkeypatch, tmp_path, owner_type="user")
    calls = []

    def fake_post(url, **kwargs):
        del url
        calls.append(kwargs["json"])
        return FakeResponse(project_page([], owner_field="user"))

    monkeypatch.setattr(requests, "post", fake_post)
    result = json.loads(GitHubProjectReader().invoke({}, {}))

    assert result["ok"] is True
    assert "user(login: $owner)" in calls[0]["query"]
    assert "organization(login: $owner)" not in calls[0]["query"]


def test_reader_fails_before_network_when_fixed_config_is_missing(monkeypatch, tmp_path):
    set_github_config(monkeypatch, tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN")
    called = False

    def fake_post(url, **kwargs):
        del url, kwargs
        nonlocal called
        called = True

    monkeypatch.setattr(requests, "post", fake_post)
    result = json.loads(GitHubProjectReader().invoke({}, {}))

    assert result == {"error": "GITHUB_TOKEN is required", "ok": False}
    assert called is False


def test_reader_sanitizes_graphql_and_transport_errors(monkeypatch, tmp_path):
    set_github_config(monkeypatch, tmp_path)
    upstream_secret = "sensitive-repository-name"
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: FakeResponse({"errors": [{"message": upstream_secret}]}),
    )
    graphql_result = json.loads(GitHubProjectReader().invoke({}, {}))
    assert graphql_result["ok"] is False
    assert upstream_secret not in graphql_result["error"]

    def fail_post(*args, **kwargs):
        raise requests.Timeout(f"timeout with github-secret-token and {upstream_secret}")

    monkeypatch.setattr(requests, "post", fail_post)
    transport_result = json.loads(GitHubProjectReader().invoke({}, {}))
    assert transport_result == {"error": "GitHub GraphQL request failed", "ok": False}


def test_reader_stops_at_item_limit_without_requesting_an_extra_page(monkeypatch, tmp_path):
    set_github_config(monkeypatch, tmp_path, max_items="1")
    calls = []

    def fake_post(url, **kwargs):
        del url
        calls.append(kwargs)
        return FakeResponse(project_page([issue_node()], has_next=True, cursor="next-page"))

    monkeypatch.setattr(requests, "post", fake_post)
    result = json.loads(GitHubProjectReader().invoke({}, {}))

    assert result["ok"] is False
    assert "1 item limit" in result["error"]
    assert len(calls) == 1
    assert calls[0]["json"]["variables"]["pageSize"] == 1


def test_reader_rejects_repeated_pagination_cursor(monkeypatch, tmp_path):
    set_github_config(monkeypatch, tmp_path, max_items="51")
    responses = [
        FakeResponse(project_page([], has_next=True, cursor="same-cursor")),
        FakeResponse(project_page([], has_next=True, cursor="same-cursor")),
    ]
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: responses.pop(0))

    result = json.loads(GitHubProjectReader().invoke({}, {}))

    assert result == {"error": "GitHub GraphQL returned an invalid pagination cursor", "ok": False}


def test_reader_fails_instead_of_truncating_nested_values(monkeypatch, tmp_path):
    set_github_config(monkeypatch, tmp_path)
    node = issue_node()
    node["content"]["labels"] = {"totalCount": 101, "nodes": [{"name": "one"}]}
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FakeResponse(project_page([node])))

    result = json.loads(GitHubProjectReader().invoke({}, {}))

    assert result == {"error": "A GitHub Project item exceeds a nested value safety limit", "ok": False}
