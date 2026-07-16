import base64
import json

from coded_tools.colleague.github_public_read import GitHubFileRead
from coded_tools.colleague.github_public_read import GitHubIssueRead
from coded_tools.colleague.github_public_read import GitHubPullRequestRead
from coded_tools.colleague.github_public_read import GitHubRepositoryTree


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


def configure(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv(
        "GITHUB_READ_ALLOWED_REPOSITORIES",
        "cognizant-ai-lab/neuro-san,cognizant-ai-lab/neuro-san-studio",
    )
    monkeypatch.setenv("COLLEAGUE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))


def public_repository():
    return {"private": False, "default_branch": "main"}


def test_issue_reader_returns_body_and_metadata(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)

    def fake_get(url, **kwargs):
        del kwargs
        if url.endswith("/repos/cognizant-ai-lab/neuro-san"):
            return Response(public_repository())
        return Response(
            {
                "title": "Loop support",
                "body": "Acceptance: the agent wakes on schedule. PR #839",
                "state": "open",
                "html_url": "https://github.com/cognizant-ai-lab/neuro-san/issues/900",
                "user": {"login": "author"},
                "assignees": [{"login": "owner"}],
                "labels": [{"name": "feature"}],
                "comments": 3,
            }
        )

    monkeypatch.setattr("coded_tools.colleague.github_public_read.requests.get", fake_get)
    result = json.loads(
        GitHubIssueRead().invoke({"owner": "cognizant-ai-lab", "repo": "neuro-san", "number": 900}, {})
    )

    assert result["ok"] is True
    assert result["body"].startswith("Acceptance:")
    assert result["assignees"] == ["owner"]
    assert result["labels"] == ["feature"]


def test_reader_rejects_unallowlisted_repo_without_network(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)

    def unexpected_get(*args, **kwargs):
        raise AssertionError((args, kwargs))

    monkeypatch.setattr("coded_tools.colleague.github_public_read.requests.get", unexpected_get)
    result = json.loads(GitHubIssueRead().invoke({"owner": "someone", "repo": "private-looking", "number": 1}, {}))

    assert result == {"error": "Repository is not in the public read allowlist", "ok": False}


def test_reader_rejects_private_repository(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "coded_tools.colleague.github_public_read.requests.get",
        lambda *args, **kwargs: Response({"private": True}),
    )

    result = json.loads(GitHubIssueRead().invoke({"owner": "cognizant-ai-lab", "repo": "neuro-san", "number": 1}, {}))

    assert result == {"error": "Repository is not public", "ok": False}


def test_pull_request_reader_returns_bounded_patch_context(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)

    def fake_get(url, **kwargs):
        del kwargs
        if url.endswith("/repos/cognizant-ai-lab/neuro-san"):
            return Response(public_repository())
        if url.endswith("/pulls/839/files"):
            return Response(
                [
                    {
                        "filename": "core/events.py",
                        "status": "modified",
                        "additions": 4,
                        "deletions": 1,
                        "patch": "@@ -1 +1 @@\n-old\n+new",
                    }
                ]
            )
        return Response(
            {
                "title": "Add event loops",
                "body": "Closes #800",
                "state": "closed",
                "merged": True,
                "html_url": "https://github.com/example/pull/839",
                "user": {"login": "author"},
                "base": {"ref": "main", "sha": "base"},
                "head": {"ref": "events", "sha": "head"},
                "changed_files": 1,
            }
        )

    monkeypatch.setattr("coded_tools.colleague.github_public_read.requests.get", fake_get)
    result = json.loads(
        GitHubPullRequestRead().invoke({"owner": "cognizant-ai-lab", "repo": "neuro-san", "number": 839}, {})
    )

    assert result["ok"] is True
    assert result["merged"] is True
    assert result["head_sha"] == "head"
    assert result["files"][0]["path"] == "core/events.py"
    assert "+new" in result["files"][0]["patch"]


def test_tree_and_file_read_are_ref_scoped(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    source = "def wake():\n    return True\n"

    def fake_get(url, **kwargs):
        params = kwargs.get("params") or {}
        if url.endswith("/repos/cognizant-ai-lab/neuro-san"):
            return Response(public_repository())
        if "/git/trees/" in url:
            assert params == {"recursive": "1"}
            return Response(
                {
                    "truncated": False,
                    "tree": [
                        {"path": "core/events.py", "type": "blob", "size": len(source), "sha": "abc"},
                        {"path": "docs", "type": "tree", "sha": "def"},
                    ],
                }
            )
        assert params == {"ref": "abc123"}
        return Response(
            {
                "type": "file",
                "size": len(source),
                "sha": "abc",
                "encoding": "base64",
                "content": base64.b64encode(source.encode()).decode(),
            }
        )

    monkeypatch.setattr("coded_tools.colleague.github_public_read.requests.get", fake_get)
    tree = json.loads(
        GitHubRepositoryTree().invoke(
            {"owner": "cognizant-ai-lab", "repo": "neuro-san", "ref": "abc123", "path_prefix": "core"}, {}
        )
    )
    file_result = json.loads(
        GitHubFileRead().invoke(
            {"owner": "cognizant-ai-lab", "repo": "neuro-san", "ref": "abc123", "path": "core/events.py"}, {}
        )
    )

    assert tree["entries"] == [{"path": "core/events.py", "sha": "abc", "size": len(source), "type": "blob"}]
    assert file_result["content"] == source


def test_file_reader_rejects_path_traversal_before_content_request(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(public_repository())

    monkeypatch.setattr("coded_tools.colleague.github_public_read.requests.get", fake_get)
    result = json.loads(
        GitHubFileRead().invoke({"owner": "cognizant-ai-lab", "repo": "neuro-san", "path": "../secret"}, {})
    )

    assert result == {"error": "path is invalid", "ok": False}
    assert len(calls) == 1
