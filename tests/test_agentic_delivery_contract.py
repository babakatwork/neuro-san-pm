from pathlib import Path

from pyhocon import ConfigFactory

ROOT = Path(__file__).resolve().parents[1]


def tools_by_name(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "validation-only")
    network = ConfigFactory.parse_string(
        (ROOT / "registries" / "product_colleague.hocon").read_text(encoding="utf-8"),
        basedir=ROOT,
    )
    return {tool["name"]: tool for tool in network["tools"]}


def test_top_agent_delegates_without_delivery_capabilities(monkeypatch):
    tools = tools_by_name(monkeypatch)
    front = tools["ProductColleague"]
    assert "AgenticDeliveryManager" in front["tools"]
    forbidden = {
        "DeliveryCandidateTriage",
        "CoderSupervisor",
        "DeliveryPullRequestReviewer",
        "GitHubPullRequestDeliveryContext",
        "GitHubDeliveryWrite",
        "CoderForkBoundary",
        "SlackCoderApproval",
        "coding_agent",
    }
    assert not forbidden & set(front["tools"])
    assert "GitHubDeliveryCandidates" in front["tools"]
    assert "GitHubIssueDeliveryContext" in front["tools"]
    normalized_instructions = " ".join(front["instructions"].split())
    assert "AgenticDeliveryManager is the sole down-chain owner" in normalized_instructions
    assert "must be passed onward" in normalized_instructions
    assert "whenever the candidate scan returned a candidate or active handoff" in normalized_instructions


def test_top_agent_owns_product_judgment_and_manager_owns_execution(monkeypatch):
    tools = tools_by_name(monkeypatch)
    manager = tools["AgenticDeliveryManager"]
    assert set(manager["tools"]) == {
        "RuntimeConfig",
        "DeliveryCandidateTriage",
        "CoderSupervisor",
        "DeliveryPullRequestReviewer",
    }
    assert "delivery_brief" in manager["function"]["parameters"]["required"]
    assert "sole nomination from ProductColleague" in tools["DeliveryCandidateTriage"]["instructions"]
    assert "Delegate narrow resolution and approval work" in " ".join(manager["instructions"].split())
    assert tools["CoderSupervisor"]["tools"][-1] == "coding_agent"
    assert tools["coding_agent"]["class"] == "coded_tools.coding_agent.coding_agent.CodingAgent"
    assert "operation=start" in tools["CoderSupervisor"]["instructions"]
    assert "fresh session" in tools["CoderSupervisor"]["instructions"]
    assert "push only to origin" in tools["CoderSupervisor"]["instructions"]
    assert "verify_pull_request" in tools["CoderSupervisor"]["instructions"]
    assert tools["CoderForkBoundary"]["class"] == "coded_tools.colleague.fork_delivery.CoderForkBoundary"


def test_delivery_write_surface_contains_no_merge_close_or_approval(monkeypatch):
    tools = tools_by_name(monkeypatch)
    write = tools["GitHubDeliveryWrite"]
    operations = set(write["function"]["parameters"]["properties"]["operation"]["enum"])
    assert operations == {
        "comment_issue",
        "assign_issue_to_pm",
        "assign_issue_to_coder",
        "move_issue_status",
        "comment_pr",
        "assign_pr_to_pm",
        "assign_pr_to_coder",
        "request_human_reviewers",
    }
    assert not {"merge_pr", "close_issue", "close_pr", "approve_pr"} & operations
    assert "proposal_id" in write["function"]["parameters"]["required"]


def test_approval_and_candidate_tools_are_host_bounded(monkeypatch):
    tools = tools_by_name(monkeypatch)
    approval = tools["SlackCoderApproval"]
    assert set(approval["function"]["parameters"]["properties"]["action"]["enum"]) == {
        "propose",
        "replies",
        "decide",
        "clarify",
        "status",
    }
    assert approval["function"]["parameters"]["properties"]["decision"]["enum"] == ["approve", "reject"]
    triage = " ".join(tools["DeliveryCandidateTriage"]["instructions"].split())
    assert "Interpret those verified exact-thread replies conversationally" in triage
    assert "host re-fetches and independently verifies the message provenance" in triage
    assert "SlackCoderApproval" not in tools["CoderSupervisor"]["tools"]
    assert "SlackCoderApproval" not in tools["DeliveryPullRequestReviewer"]["tools"]
    assert "do not post it to Slack" in tools["DeliveryPullRequestReviewer"]["instructions"]
    assert tools["GitHubDeliveryCandidates"]["function"]["parameters"]["properties"]["request"]["enum"] == [
        "scan_configured_project"
    ]
    assert "owner" not in tools["GitHubDeliveryCandidates"]["function"]["parameters"]["properties"]
