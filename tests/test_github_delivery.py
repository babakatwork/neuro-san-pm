import json

from coded_tools.colleague.github_delivery import GitHubDeliveryCandidates
from coded_tools.colleague.github_delivery import GitHubDeliveryWrite
from coded_tools.colleague.github_delivery import GitHubIssueDeliveryContext
from coded_tools.colleague.github_delivery import GitHubPullRequestDeliveryContext


class Response:
    def __init__(self, payload=None, status_code=200):
        self.payload = {} if payload is None else payload
        self.status_code = status_code

    def json(self):
        return self.payload


def configure(monkeypatch, tmp_path, *, writes=True):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_PM_TOKEN", "pm-secret-token")
    monkeypatch.setenv("GITHUB_DELIVERY_ALLOWED_REPOSITORIES", "cognizant-ai-lab/neuro-san")
    monkeypatch.setenv("GITHUB_DELIVERY_PM_LOGIN", "product-manager")
    monkeypatch.setenv("GITHUB_DELIVERY_CODER_LOGIN", "agentic-coder")
    monkeypatch.setenv("GITHUB_DELIVERY_HUMAN_REVIEWERS", "alice,bob")
    monkeypatch.setenv("GITHUB_DELIVERY_WRITE_ENABLED", "true" if writes else "false")
    monkeypatch.setenv("GITHUB_DELIVERY_LEDGER_PATH", str(tmp_path / "delivery.json"))
    approval_path = tmp_path / "approval.json"
    monkeypatch.setenv("AGENTIC_DELIVERY_STATE_PATH", str(approval_path))
    approval_path.write_text(
        json.dumps(
            {
                "version": 1,
                "proposals": {
                    "a" * 24: {
                        "proposal_id": "a" * 24,
                        "issue_url": "https://github.com/cognizant-ai-lab/neuro-san/issues/42",
                        "issue_number": 42,
                        "state": "approved",
                    }
                },
            }
        )
    )
    monkeypatch.setenv("COLLEAGUE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("GITHUB_PROJECT_ID", "PVT_configured")
    monkeypatch.setenv("GITHUB_PROJECT_OWNER", "cognizant-ai-lab")
    monkeypatch.setenv("GITHUB_PROJECT_OWNER_TYPE", "org")
    monkeypatch.setenv("GITHUB_PROJECT_NUMBER", "7")
    monkeypatch.setenv("GITHUB_PROJECT_STATUS_FIELD_ID", "PVTSSF_status")
    monkeypatch.setenv("AGENTIC_DELIVERY_ELIGIBLE_STATUSES", "Backlog,To Do")
    monkeypatch.setenv("AGENTIC_DELIVERY_STALE_AFTER_DAYS", "14")
    monkeypatch.setenv("AGENTIC_DELIVERY_MAX_CANDIDATES", "50")
    monkeypatch.setenv(
        "GITHUB_PROJECT_STATUS_OPTIONS_JSON",
        json.dumps({"In Review": "option_review", "In Progress": "option_progress"}),
    )


def invoke(tool, operation, **extra):
    args = {
        "owner": "cognizant-ai-lab",
        "repo": "neuro-san",
        "number": 42,
        "operation": operation,
        "idempotency_key": f"test-key-{operation}",
        "proposal_id": "a" * 24,
        **extra,
    }
    if "_pr" in operation or operation == "request_human_reviewers":
        args["source_issue_number"] = 42
    return json.loads(tool.invoke(args, {}))


def board_item(number, status, assignees, *, updated_at="2000-01-01T00:00:00Z", labels=None, repo=None):
    repository = repo or "cognizant-ai-lab/neuro-san"
    return {
        "id": f"PVTI_{number}",
        "type": "Issue",
        "repository": repository,
        "number": str(number),
        "title": f"Ticket {number}",
        "url": f"https://github.com/{repository}/issues/{number}",
        "status": status,
        "assignees": assignees,
        "labels": labels or [],
        "updated_at": updated_at,
    }


def test_candidates_are_fixed_board_bounded_policy_and_include_active_handoffs(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENTIC_DELIVERY_REQUIRED_LABEL", "agent-ready")
    project = {
        "project": {"owner": "cognizant-ai-lab", "number": 7},
        "items": [
            board_item(1, "Backlog", [], labels=["agent-ready"]),
            board_item(2, "To Do", ["human"], labels=["agent-ready"]),
            board_item(3, "To Do", ["human"], updated_at="2999-01-01T00:00:00Z", labels=["agent-ready"]),
            board_item(4, "In Progress", ["agentic-coder"]),
            board_item(42, "In Review", ["product-manager", "human-owner"]),
            board_item(6, "In Progress", ["unrelated-human"]),
            board_item(7, "Backlog", [], labels=["other"]),
            board_item(8, "Backlog", [], labels=["agent-ready"], repo="other/repo"),
        ],
    }
    monkeypatch.setattr(
        "coded_tools.colleague.github_delivery.GitHubProjectReader._read_project",
        lambda config: project,
    )

    result = json.loads(GitHubDeliveryCandidates().invoke({"owner": "attacker", "project": 999}, {}))

    assert result["ok"] is True
    assert [(item["number"], item["reason"]) for item in result["candidates"]] == [
        (1, "unassigned"),
        (2, "stale"),
    ]
    assert [item["number"] for item in result["active_handoffs"]] == [4, 42]
    assert result["active_handoffs"][1]["proposal_id"] == "a" * 24
    assert result["active_handoffs"][1]["proposal_state"] == "approved"
    assert result["candidates"][0]["project_item_id"] == "PVTI_1"
    assert result["candidates"][0]["owner"] == "cognizant-ai-lab"
    assert result["candidates"][0]["repo"] == "neuro-san"


def test_candidates_accept_todo_status_alias(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENTIC_DELIVERY_ELIGIBLE_STATUSES", "Todo")
    project = {
        "project": {"owner": "cognizant-ai-lab", "number": 7},
        "items": [board_item(1, "To Do", [], labels=[])],
    }
    monkeypatch.setattr(
        "coded_tools.colleague.github_delivery.GitHubProjectReader._read_project",
        lambda config: project,
    )
    result = json.loads(GitHubDeliveryCandidates().invoke({}, {}))
    assert result["ok"] is True
    assert [item["number"] for item in result["candidates"]] == [1]


def test_writes_are_default_off_and_do_not_contact_github(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, writes=False)
    monkeypatch.setattr(
        "coded_tools.colleague.github_delivery.requests.request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError((args, kwargs))),
    )

    result = invoke(GitHubDeliveryWrite(), "comment_issue", body="Plan")

    assert result == {"error": "GitHub delivery writes are disabled", "ok": False}


def test_every_write_requires_matching_approved_proposal_before_network(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(
        "coded_tools.colleague.github_delivery.requests.request",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    tool = GitHubDeliveryWrite()
    base = {
        "owner": "cognizant-ai-lab",
        "repo": "neuro-san",
        "number": 42,
        "operation": "comment_issue",
        "idempotency_key": "approval-test-key",
        "body": "Plan",
    }

    missing = json.loads(tool.invoke(base, {}))
    wrong_issue = json.loads(tool.invoke({**base, "proposal_id": "a" * 24, "number": 43}, {}))
    wrong_source = json.loads(
        tool.invoke(
            {
                **base,
                "proposal_id": "a" * 24,
                "operation": "comment_pr",
                "source_issue_number": 99,
            },
            {},
        )
    )

    assert missing == {"error": "A valid approved proposal_id is required", "ok": False}
    assert wrong_issue == {"error": "Approved proposal does not match this issue", "ok": False}
    assert wrong_source == {
        "error": "Approved proposal does not match this pull request workflow",
        "ok": False,
    }
    assert calls == []


def test_rejects_unallowlisted_repository_and_unknown_operation_before_network(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr("coded_tools.colleague.github_delivery.requests.request", lambda *a, **k: calls.append((a, k)))
    tool = GitHubDeliveryWrite()

    not_allowed = json.loads(
        tool.invoke(
            {
                "owner": "attacker",
                "repo": "repo",
                "number": 1,
                "operation": "comment_issue",
                "idempotency_key": "safe-key-123",
                "body": "hello",
            },
            {},
        )
    )
    unknown = invoke(tool, "merge_pr")

    assert not_allowed == {"error": "Repository is not in the delivery allowlist", "ok": False}
    assert unknown == {"error": "GitHub delivery operation is not allowed", "ok": False}
    assert calls == []
    assert not ({"merge_pr", "approve_pr", "close_issue", "close_pr"} & GitHubDeliveryWrite.OPERATIONS)


def test_issue_comment_is_bounded_audited_and_idempotent(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response({"html_url": "https://github.com/cognizant-ai-lab/neuro-san/issues/42#issuecomment-1"}, 201)

    monkeypatch.setattr("coded_tools.colleague.github_delivery.requests.request", request)
    tool = GitHubDeliveryWrite()

    first = invoke(tool, "comment_issue", body="Implementation plan")
    second = invoke(tool, "comment_issue", body="Implementation plan")

    assert first["ok"] is True and first["duplicate"] is False
    assert second["ok"] is True and second["duplicate"] is True
    assert len(calls) == 1
    method, url, kwargs = calls[0]
    assert method == "POST"
    assert url.endswith("/repos/cognizant-ai-lab/neuro-san/issues/42/comments")
    assert kwargs["json"] == {"body": "Implementation plan"}
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Authorization"] == "Bearer pm-secret-token"
    assert "pm-secret-token" not in (tmp_path / "audit.jsonl").read_text()


def test_idempotency_key_cannot_be_reused_for_a_different_action(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "coded_tools.colleague.github_delivery.requests.request", lambda *a, **k: Response({"html_url": "url"}, 201)
    )
    tool = GitHubDeliveryWrite()
    assert invoke(tool, "comment_issue", body="First")["ok"] is True

    result = invoke(tool, "comment_issue", body="Changed")

    assert result == {"error": "idempotency_key was already used differently", "ok": False}


def test_assignments_can_only_target_configured_pm_or_coder(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("json")))
        if method == "GET":
            return Response(
                {
                    "assignees": [
                        {"login": "product-manager"},
                        {"login": "agentic-coder"},
                        {"login": "human-owner"},
                    ]
                }
            )
        return Response({"assignees": kwargs["json"]["assignees"]})

    monkeypatch.setattr("coded_tools.colleague.github_delivery.requests.request", request)
    tool = GitHubDeliveryWrite()

    coder = invoke(tool, "assign_issue_to_coder")
    pm = invoke(tool, "assign_pr_to_pm")

    assert coder["assignee"] == "agentic-coder"
    assert pm["assignee"] == "product-manager"
    assert coder["assignees"] == ["agentic-coder", "human-owner"]
    assert pm["assignees"] == ["human-owner", "product-manager"]
    assert calls[1][0] == "PATCH" and calls[1][2] == {"assignees": ["agentic-coder", "human-owner"]}
    assert calls[3][0] == "PATCH" and calls[3][2] == {"assignees": ["human-owner", "product-manager"]}
    assert all(url.endswith("/issues/42") for _, url, _ in calls)


def test_human_review_requests_are_allowlisted(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(
        "coded_tools.colleague.github_delivery.requests.request",
        lambda method, url, **kwargs: calls.append((method, url, kwargs["json"])) or Response({}, 201),
    )
    tool = GitHubDeliveryWrite()

    rejected = invoke(tool, "request_human_reviewers", reviewers=["mallory"])
    accepted = invoke(tool, "request_human_reviewers", reviewers=["bob", "alice"])

    assert rejected == {"error": "Every reviewer must be in the human reviewer allowlist", "ok": False}
    assert accepted["reviewers"] == ["alice", "bob"]
    assert calls == [
        (
            "POST",
            "https://api.github.com/repos/cognizant-ai-lab/neuro-san/pulls/42/requested_reviewers",
            {"reviewers": ["alice", "bob"]},
        )
    ]


def test_project_move_uses_only_configured_ids_and_status_options(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    graphql_calls = []

    def graphql(url, **kwargs):
        graphql_calls.append((url, kwargs))
        if kwargs["json"]["operationName"] == "ValidateConfiguredProjectItem":
            return Response(
                {
                    "data": {
                        "node": {
                            "project": {"id": "PVT_configured"},
                            "content": {
                                "number": 42,
                                "repository": {"nameWithOwner": "cognizant-ai-lab/neuro-san"},
                            },
                        }
                    }
                }
            )
        return Response({"data": {"ok": True}})

    monkeypatch.setattr("coded_tools.colleague.github_delivery.requests.post", graphql)
    tool = GitHubDeliveryWrite()

    rejected = invoke(tool, "move_issue_status", project_item_id="PVTI_ticket", status="Done")
    accepted = invoke(tool, "move_issue_status", project_item_id="PVTI_ticket", status="In Review")

    assert rejected == {"error": "status is not in the configured project status allowlist", "ok": False}
    assert accepted["status"] == "In Review"
    assert graphql_calls[0][1]["json"]["operationName"] == "ValidateConfiguredProjectItem"
    payload = graphql_calls[1][1]["json"]
    assert payload["operationName"] == "MoveConfiguredProjectItem"
    assert payload["variables"] == {
        "project": "PVT_configured",
        "item": "PVTI_ticket",
        "field": "PVTSSF_status",
        "option": "option_review",
    }
    assert "merge" not in payload["query"].lower()


def test_delivery_context_reads_issue_comments(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)

    def request(method, url, **kwargs):
        assert method == "GET"
        if url.endswith("/issues/42"):
            return Response(
                {
                    "title": "Defined task",
                    "body": "Acceptance criteria",
                    "state": "open",
                    "assignees": [{"login": "agentic-coder"}],
                }
            )
        return Response([{"user": {"login": "agentic-coder"}, "body": "Plan", "created_at": "now", "html_url": "url"}])

    monkeypatch.setattr("coded_tools.colleague.github_delivery.requests.request", request)
    result = json.loads(
        GitHubIssueDeliveryContext().invoke({"owner": "cognizant-ai-lab", "repo": "neuro-san", "number": 42}, {})
    )

    assert result["ok"] is True
    assert result["assignees"] == ["agentic-coder"]
    assert result["comments"][0]["body"] == "Plan"


def test_delivery_context_reads_pr_conversation_and_reviews(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)

    def request(method, url, **kwargs):
        assert method == "GET"
        if url.endswith("/pulls/42"):
            return Response({"title": "PR", "body": "Fix", "state": "open", "draft": False, "merged": False})
        if "/reviews?" in url:
            return Response([{"user": {"login": "alice"}, "state": "CHANGES_REQUESTED", "body": "Test edge case"}])
        return Response([{"user": {"login": "product-manager"}, "body": "Review summary"}])

    monkeypatch.setattr("coded_tools.colleague.github_delivery.requests.request", request)
    result = json.loads(
        GitHubPullRequestDeliveryContext().invoke({"owner": "cognizant-ai-lab", "repo": "neuro-san", "number": 42}, {})
    )

    assert result["ok"] is True
    assert result["comments"][0]["body"] == "Review summary"
    assert result["reviews"][0]["state"] == "CHANGES_REQUESTED"


def test_delivery_context_redacts_local_paths_and_secrets(monkeypatch, tmp_path):
    from coded_tools.colleague.github_delivery import GitHubIssueDeliveryContext
    configure(monkeypatch, tmp_path)
    issue = {"title": "Fix /Users/alice/project", "body": "token=ghp_abcdefghijklmnop1234 and /home/alice/x"}
    comments = [{"user": {"login": "alice"}, "body": "See /private/var/tmp/work"}]
    monkeypatch.setattr("coded_tools.colleague.github_delivery._Client.request", lambda self, method, path, **kwargs: issue if "/issues/42" in path and "comments" not in path else comments)
    result = GitHubIssueDeliveryContext().invoke({"owner": "cognizant-ai-lab", "repo": "neuro-san", "number": "42"}, {})
    import json
    payload = json.loads(result)
    text = json.dumps(payload)
    assert "/Users/" not in text and "/home/" not in text and "ghp_" not in text
    assert "<redacted-local-path>" in text and "<redacted-secret>" in text
