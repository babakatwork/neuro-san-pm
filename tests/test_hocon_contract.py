from pathlib import Path

from pyhocon import ConfigFactory

ROOT = Path(__file__).resolve().parents[1]


def test_event_network_and_native_periodic_manifest(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "validation-only")
    network_path = ROOT / "registries" / "product_colleague.hocon"
    network = ConfigFactory.parse_string(network_path.read_text(encoding="utf-8"), basedir=ROOT)
    manifest = ConfigFactory.parse_file(ROOT / "registries" / "manifest.hocon")

    frontman = network["tools"][0]
    manifest_entries = {str(key).strip('"'): value for key, value in manifest.items()}
    interaction = manifest_entries["product_colleague.hocon"]["periodic"]["interactions"][0]
    assert frontman["function"]["invocation"] == "event"
    assert interaction["enable"] is True
    assert "cron_schedule" in interaction
    assert "sly_data" not in interaction


def test_mcp_is_read_only_and_uses_current_header_key(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "validation-only")
    config = ConfigFactory.parse_file(ROOT / "mcp" / "mcp_info.hocon")

    github_entries = {str(key).strip('"'): value for key, value in config.items() if "githubcopilot.com" in key}
    assert github_entries
    assert all("/readonly" in url for url in github_entries)
    assert all("http_headers" in value for value in github_entries.values())
    assert all("headers" not in value for value in github_entries.values())
    assert set(github_entries["https://api.githubcopilot.com/mcp/x/projects/readonly"]["tools"]) == {
        "projects_get",
        "projects_list",
    }


def test_sample_uses_host_scoped_github_snapshot(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "validation-only")
    network_path = ROOT / "registries" / "product_colleague.hocon"
    network = ConfigFactory.parse_string(network_path.read_text(encoding="utf-8"), basedir=ROOT)

    tools = {tool["name"]: tool for tool in network["tools"]}
    analyst = tools["KanbanAnalyst"]
    snapshot = tools["GitHubKanbanSnapshot"]
    assert analyst["tools"] == ["GitHubKanbanSnapshot"]
    assert snapshot["name"] == "GitHubKanbanSnapshot"
    assert snapshot["function"]["parameters"]["properties"]["request"]["enum"] == [
        "snapshot_configured_project"
    ]
    assert "owner and project number come only" in analyst["instructions"]
    assert "existing authoritative Kanban board" in analyst["instructions"]
    assert "Never recommend" in analyst["instructions"]
    assert "snapshot you produce is only internal monitoring state" in analyst["instructions"]
    assert "bounded attention items instead of every card" in analyst["instructions"]

    frontman = tools["ProductColleague"]
    assert "already exists and is the team's authoritative" in frontman["instructions"]
    assert "never propose or attempt to" in frontman["instructions"]
    assert "cannot modify GitHub" in frontman["instructions"]


def test_callable_function_schemas_have_at_least_one_property(monkeypatch):
    """Catch a Neuro SAN execution constraint that its HOCON validator misses."""
    monkeypatch.setenv("GITHUB_TOKEN", "validation-only")
    network_path = ROOT / "registries" / "product_colleague.hocon"
    network = ConfigFactory.parse_string(network_path.read_text(encoding="utf-8"), basedir=ROOT)

    for tool in network["tools"]:
        parameters = tool["function"].get("parameters", None)
        if parameters is not None:
            assert parameters.get("properties"), tool["name"]


def test_gmail_tools_are_separate_and_write_is_policy_gated(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "validation-only")
    network = ConfigFactory.parse_string(
        (ROOT / "registries" / "product_colleague.hocon").read_text(encoding="utf-8"), basedir=ROOT
    )
    tools = {tool["name"]: tool for tool in network["tools"]}
    assert tools["GmailAssistant"]["tools"] == ["GmailSearch", "GmailRead", "GmailSend"]
    assert "trusted Slack request" in tools["GmailAssistant"]["instructions"]
    assert tools["GmailSend"]["class"].endswith("gmail_send.GmailSend")


def test_top_agent_delegates_product_judgment_but_keeps_side_effects(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "validation-only")
    network = ConfigFactory.parse_string(
        (ROOT / "registries" / "product_colleague.hocon").read_text(encoding="utf-8"), basedir=ROOT
    )
    tools = {tool["name"]: tool for tool in network["tools"]}
    frontman = tools["ProductColleague"]
    advisor = tools["ProductManagerAdvisor"]

    assert "ProductManagerAdvisor" in frontman["tools"]
    assert "delegate product judgment" in frontman["instructions"]
    assert "Call ProductManagerAdvisor exactly once" in frontman["instructions"]
    assert advisor["tools"] == []
    assert "no tools and no side-effect authority" in advisor["instructions"]
    assert "SlackPost" not in advisor["tools"]
    assert "ColleagueState" not in advisor["tools"]
