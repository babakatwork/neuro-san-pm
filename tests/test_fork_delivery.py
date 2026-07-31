import json
import os
import stat
import subprocess
from pathlib import Path

from coded_tools.colleague.fork_delivery import CoderForkBoundary


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


def configure(monkeypatch, workspace):
    monkeypatch.setenv("GITHUB_DELIVERY_ALLOWED_REPOSITORIES", "upstream/project")
    monkeypatch.setenv("GITHUB_DELIVERY_CODER_LOGIN", "coder-bot")
    monkeypatch.setenv("GITHUB_PM_TOKEN", "pm-token")
    monkeypatch.setenv("GITHUB_CODER_TOKEN", "coder-token")
    monkeypatch.setenv("CODING_AGENT_ALLOWED_WORKSPACES", str(workspace.parent))
    monkeypatch.setenv("CODING_AGENT_PRIMARY_WORKSPACE", str(workspace))


def repository(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def permission_response(url, headers):
    token = headers["Authorization"]
    if url.endswith("/repos/upstream/project"):
        return Response({"full_name": "upstream/project", "permissions": {"push": False}})
    if url.endswith("/repos/coder-bot/project") and token == "Bearer coder-token":
        return Response(
            {
                "full_name": "coder-bot/project",
                "fork": True,
                "parent": {"full_name": "upstream/project"},
                "permissions": {"push": True},
            }
        )
    raise AssertionError((url, token))


def test_prepare_workspace_enforces_permissions_and_rewrites_remotes(monkeypatch, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    repository(workspace, "init")
    repository(workspace, "remote", "add", "origin", "git@github.com:upstream/project.git")
    configure(monkeypatch, workspace)
    monkeypatch.setattr(
        "coded_tools.colleague.fork_delivery.requests.get",
        lambda url, *, headers, **kwargs: permission_response(url, headers),
    )

    result = json.loads(
        CoderForkBoundary().invoke(
            {
                "operation": "prepare_workspace",
                "owner": "upstream",
                "repo": "project",
                "workspace": str(workspace),
            },
            {},
        )
    )

    assert result["ok"] is True
    assert repository(workspace, "config", "--get", "remote.origin.url") == "https://github.com/coder-bot/project.git"
    assert repository(workspace, "config", "--get", "remote.upstream.url") == "https://github.com/upstream/project.git"


def test_prepare_workspace_rejects_any_upstream_push_permission(monkeypatch, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    repository(workspace, "init")
    repository(workspace, "remote", "add", "origin", "https://github.com/upstream/project.git")
    configure(monkeypatch, workspace)

    def get(url, *, headers, **kwargs):
        response = permission_response(url, headers)
        if url.endswith("/repos/upstream/project") and headers["Authorization"] == "Bearer coder-token":
            response.payload["permissions"]["push"] = True
        return response

    monkeypatch.setattr("coded_tools.colleague.fork_delivery.requests.get", get)
    result = json.loads(
        CoderForkBoundary().invoke(
            {
                "operation": "prepare_workspace",
                "owner": "upstream",
                "repo": "project",
                "workspace": str(workspace),
            },
            {},
        )
    )
    assert result == {"error": "PM and coder must not have upstream push permission", "ok": False}
    assert repository(workspace, "config", "--get", "remote.origin.url") == "https://github.com/upstream/project.git"


def test_verify_pr_requires_open_cross_fork_default_branch(monkeypatch, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    repository(workspace, "init")
    configure(monkeypatch, workspace)
    pull = {
        "state": "open",
        "merged": False,
        "base": {
            "ref": "main",
            "repo": {"full_name": "upstream/project", "default_branch": "main"},
        },
        "head": {"ref": "codex/issue-42", "repo": {"full_name": "coder-bot/project"}},
    }
    fork = {
        "fork": True,
        "parent": {"full_name": "upstream/project"},
    }
    monkeypatch.setattr(
        "coded_tools.colleague.fork_delivery.requests.get",
        lambda url, **kwargs: Response(pull if "/pulls/" in url else fork),
    )

    result = json.loads(
        CoderForkBoundary().invoke(
            {
                "operation": "verify_pull_request",
                "owner": "upstream",
                "repo": "project",
                "pr_url": "https://github.com/upstream/project/pull/42",
            },
            {},
        )
    )
    assert result["verified"] is True
    assert result["fork_repository"] == "coder-bot/project"
    assert result["merged"] is False

    pull["head"]["repo"]["full_name"] = "upstream/project"
    rejected = json.loads(
        CoderForkBoundary().invoke(
            {
                "operation": "verify_pull_request",
                "owner": "upstream",
                "repo": "project",
                "pr_url": "https://github.com/upstream/project/pull/42",
            },
            {},
        )
    )
    assert rejected == {"error": "Pull request violates the configured fork-only boundary", "ok": False}


def test_launcher_exposes_only_coder_token_and_isolates_git_config(monkeypatch, tmp_path):
    capture = tmp_path / "capture.py"
    capture.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "print(json.dumps({k: os.getenv(k) for k in "
        "['GITHUB_TOKEN','GH_TOKEN','GITHUB_PM_TOKEN','GITHUB_CODER_TOKEN',"
        "'GIT_CONFIG_GLOBAL','GIT_CONFIG_NOSYSTEM','GIT_CONFIG_KEY_0',"
        "'GIT_ALLOW_PROTOCOL','SSH_AUTH_SOCK','NEURO_SAN_CODER_FORK_ONLY']}))\n"
    )
    capture.chmod(capture.stat().st_mode | stat.S_IXUSR)
    launcher = Path(__file__).resolve().parents[1] / "scripts" / "coder_codex_launcher.py"
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_TOKEN": "ambient-human-token",
            "GH_TOKEN": "ambient-gh-token",
            "GITHUB_PM_TOKEN": "pm-token",
            "GITHUB_CODER_TOKEN": "coder-token",
            "CODING_AGENT_REAL_CODEX_EXECUTABLE": str(capture),
            "CODING_AGENT_GIT_EMAIL": "coder@example.test",
            "SSH_AUTH_SOCK": "/tmp/ambient-agent.sock",
        }
    )
    completed = subprocess.run(
        [str(launcher), "ignored"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    child = json.loads(completed.stdout)
    assert child["GITHUB_TOKEN"] == "coder-token"
    assert child["GH_TOKEN"] == "coder-token"
    assert child["GITHUB_PM_TOKEN"] is None
    assert child["GITHUB_CODER_TOKEN"] is None
    assert child["GIT_CONFIG_GLOBAL"] == os.devnull
    assert child["GIT_CONFIG_NOSYSTEM"] == "1"
    assert child["GIT_CONFIG_KEY_0"] == "credential.helper"
    assert child["GIT_ALLOW_PROTOCOL"] == "https:file"
    assert child["SSH_AUTH_SOCK"] is None
    assert child["NEURO_SAN_CODER_FORK_ONLY"] == "true"
