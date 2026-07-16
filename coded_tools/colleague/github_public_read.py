"""Bounded read-only access to explicitly allowlisted public GitHub repositories."""

from __future__ import annotations

import asyncio
import base64
import os
import re
from typing import Any
from urllib.parse import quote

import requests
from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._runtime import append_audit
from coded_tools.colleague._runtime import json_result

API_ROOT = "https://api.github.com"
DEFAULT_REPOSITORIES = "cognizant-ai-lab/neuro-san,cognizant-ai-lab/neuro-san-studio"
NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}")
REF_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}")
MAX_BODY_CHARS = 30_000
MAX_PATCH_CHARS = 60_000
MAX_FILE_BYTES = 100_000
MAX_TREE_ENTRIES = 5_000
MAX_PR_FILES = 100


class GitHubReadError(RuntimeError):
    """Expected failure whose public text never contains upstream content."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def allowed_repository_names(value: str | None = None) -> list[str]:
    """Parse the explicit public-repository allowlist without making a request."""
    configured = value if value is not None else os.getenv("GITHUB_READ_ALLOWED_REPOSITORIES", DEFAULT_REPOSITORIES)
    repositories = sorted({item.strip() for item in configured.split(",") if item.strip()})
    if not repositories or any(
        len(parts := repository.split("/")) != 2 or any(not NAME_RE.fullmatch(part) for part in parts)
        for repository in repositories
    ):
        raise GitHubReadError(
            "invalid_allowlist",
            "GITHUB_READ_ALLOWED_REPOSITORIES must contain owner/repository names",
        )
    return repositories


class _PublicGitHubClient:
    def __init__(self) -> None:
        self.token = os.getenv("GITHUB_TOKEN", "").strip()
        if not self.token:
            raise GitHubReadError("missing_token", "GITHUB_TOKEN is required")
        self.allowed = {value.casefold() for value in allowed_repository_names()}
        try:
            self.timeout = float(os.getenv("GITHUB_HTTP_TIMEOUT_SECONDS", "15"))
        except ValueError as exc:
            raise GitHubReadError("invalid_timeout", "GITHUB_HTTP_TIMEOUT_SECONDS must be numeric") from exc
        if not 1 <= self.timeout <= 30:
            raise GitHubReadError("invalid_timeout", "GITHUB_HTTP_TIMEOUT_SECONDS must be between 1 and 30 seconds")

    @staticmethod
    def _valid_full_name(value: str) -> bool:
        parts = value.split("/")
        return len(parts) == 2 and all(NAME_RE.fullmatch(part) for part in parts)

    def repository(self, owner: object, repo: object) -> tuple[str, str, dict[str, Any]]:
        owner_text = str(owner or "").strip()
        repo_text = str(repo or "").strip()
        full_name = f"{owner_text}/{repo_text}"
        if not self._valid_full_name(full_name) or full_name.casefold() not in self.allowed:
            raise GitHubReadError("repository_not_allowed", "Repository is not in the public read allowlist")
        metadata = self.get(f"/repos/{quote(owner_text, safe='')}/{quote(repo_text, safe='')}")
        if not isinstance(metadata, dict) or metadata.get("private") is not False:
            raise GitHubReadError("repository_not_public", "Repository is not public")
        return owner_text, repo_text, metadata

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "neuro-san-team-colleague",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            response = requests.get(
                f"{API_ROOT}{path}",
                headers=headers,
                params=params,
                timeout=(5, self.timeout),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise GitHubReadError("request_failed", "GitHub REST request failed") from exc
        if response.status_code != 200:
            raise GitHubReadError("http_error", "GitHub REST request was rejected")
        try:
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise GitHubReadError("invalid_json", "GitHub REST returned invalid JSON") from exc


def _positive_number(value: object, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise GitHubReadError("invalid_number", f"{label} must be a positive integer") from exc
    if number <= 0:
        raise GitHubReadError("invalid_number", f"{label} must be a positive integer")
    return number


def _names(values: object, key: str = "login") -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted(
        {str(value.get(key, ""))[:200] for value in values[:100] if isinstance(value, dict) and value.get(key)}
    )


class _GitHubReadTool(CodedTool):
    event_name = "github_public_read"

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del sly_data
        try:
            result = self.read(_PublicGitHubClient(), args)
        except GitHubReadError as exc:
            append_audit(self.event_name, ok=False, error_code=exc.code)
            return json_result(ok=False, error=exc.message)
        append_audit(self.event_name, ok=True, **self.audit_fields(result))
        return json_result(ok=True, **result)

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)

    def read(self, client: _PublicGitHubClient, args: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def audit_fields(self, result: dict[str, Any]) -> dict[str, Any]:
        del result
        return {}


class GitHubIssueRead(_GitHubReadTool):
    """Read one issue body from an allowlisted public repository."""

    event_name = "github_issue_read"

    def read(self, client: _PublicGitHubClient, args: dict[str, Any]) -> dict[str, Any]:
        owner, repo, _metadata = client.repository(args.get("owner"), args.get("repo"))
        number = _positive_number(args.get("number"), "number")
        issue = client.get(f"/repos/{quote(owner)}/{quote(repo)}/issues/{number}")
        if not isinstance(issue, dict):
            raise GitHubReadError("invalid_response", "GitHub returned an invalid issue")
        if issue.get("pull_request"):
            raise GitHubReadError("not_an_issue", "The requested number identifies a pull request")
        milestone = issue.get("milestone")
        user = issue.get("user")
        return {
            "repository": f"{owner}/{repo}",
            "number": number,
            "title": str(issue.get("title") or "")[:500],
            "body": str(issue.get("body") or "")[:MAX_BODY_CHARS],
            "state": str(issue.get("state") or "")[:50],
            "url": str(issue.get("html_url") or "")[:1000],
            "author": str(user.get("login") or "")[:200] if isinstance(user, dict) else "",
            "assignees": _names(issue.get("assignees")),
            "labels": _names(issue.get("labels"), "name"),
            "milestone": str(milestone.get("title") or "")[:300] if isinstance(milestone, dict) else "",
            "created_at": str(issue.get("created_at") or "")[:100],
            "updated_at": str(issue.get("updated_at") or "")[:100],
            "comment_count": int(issue.get("comments") or 0),
        }

    def audit_fields(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"repository": result["repository"], "number": result["number"]}


class GitHubPullRequestRead(_GitHubReadTool):
    """Read one PR and a bounded set of changed-file patches."""

    event_name = "github_pull_request_read"

    def read(self, client: _PublicGitHubClient, args: dict[str, Any]) -> dict[str, Any]:
        owner, repo, _metadata = client.repository(args.get("owner"), args.get("repo"))
        number = _positive_number(args.get("number"), "number")
        pull = client.get(f"/repos/{quote(owner)}/{quote(repo)}/pulls/{number}")
        files = client.get(
            f"/repos/{quote(owner)}/{quote(repo)}/pulls/{number}/files",
            params={"per_page": MAX_PR_FILES},
        )
        if not isinstance(pull, dict) or not isinstance(files, list):
            raise GitHubReadError("invalid_response", "GitHub returned an invalid pull request")
        remaining_patch_chars = MAX_PATCH_CHARS
        normalized_files: list[dict[str, Any]] = []
        for value in files[:MAX_PR_FILES]:
            if not isinstance(value, dict):
                continue
            patch = str(value.get("patch") or "")[:remaining_patch_chars]
            remaining_patch_chars -= len(patch)
            normalized_files.append(
                {
                    "path": str(value.get("filename") or "")[:1000],
                    "previous_path": str(value.get("previous_filename") or "")[:1000],
                    "status": str(value.get("status") or "")[:50],
                    "additions": int(value.get("additions") or 0),
                    "deletions": int(value.get("deletions") or 0),
                    "patch": patch,
                }
            )
        user = pull.get("user") or {}
        base = pull.get("base") or {}
        head = pull.get("head") or {}
        return {
            "repository": f"{owner}/{repo}",
            "number": number,
            "title": str(pull.get("title") or "")[:500],
            "body": str(pull.get("body") or "")[:MAX_BODY_CHARS],
            "state": str(pull.get("state") or "")[:50],
            "draft": bool(pull.get("draft")),
            "merged": bool(pull.get("merged")),
            "url": str(pull.get("html_url") or "")[:1000],
            "author": str(user.get("login") or "")[:200] if isinstance(user, dict) else "",
            "base_ref": str(base.get("ref") or "")[:200] if isinstance(base, dict) else "",
            "base_sha": str(base.get("sha") or "")[:100] if isinstance(base, dict) else "",
            "head_ref": str(head.get("ref") or "")[:200] if isinstance(head, dict) else "",
            "head_sha": str(head.get("sha") or "")[:100] if isinstance(head, dict) else "",
            "changed_files": int(pull.get("changed_files") or 0),
            "files_complete": int(pull.get("changed_files") or 0) <= len(normalized_files),
            "files": normalized_files,
        }

    def audit_fields(self, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "repository": result["repository"],
            "number": result["number"],
            "file_count": len(result["files"]),
        }


class GitHubRepositoryTree(_GitHubReadTool):
    """List bounded paths at a ref in an allowlisted public repository."""

    event_name = "github_repository_tree"

    def read(self, client: _PublicGitHubClient, args: dict[str, Any]) -> dict[str, Any]:
        owner, repo, metadata = client.repository(args.get("owner"), args.get("repo"))
        ref = str(args.get("ref") or metadata.get("default_branch") or "").strip()
        if not REF_RE.fullmatch(ref) or ".." in ref:
            raise GitHubReadError("invalid_ref", "ref is invalid")
        prefix = str(args.get("path_prefix") or "").strip("/")
        if prefix and (".." in prefix.split("/") or len(prefix) > 500):
            raise GitHubReadError("invalid_path", "path_prefix is invalid")
        tree = client.get(
            f"/repos/{quote(owner)}/{quote(repo)}/git/trees/{quote(ref, safe='')}",
            params={"recursive": "1"},
        )
        if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list):
            raise GitHubReadError("invalid_response", "GitHub returned an invalid repository tree")
        candidates = [
            value
            for value in tree["tree"]
            if isinstance(value, dict)
            and str(value.get("type")) in {"blob", "tree"}
            and (not prefix or str(value.get("path", "")).startswith(prefix))
        ]
        entries = [
            {
                "path": str(value.get("path") or "")[:1000],
                "type": str(value.get("type") or "")[:20],
                "size": int(value.get("size") or 0),
                "sha": str(value.get("sha") or "")[:100],
            }
            for value in candidates[:MAX_TREE_ENTRIES]
        ]
        return {
            "repository": f"{owner}/{repo}",
            "ref": ref,
            "path_prefix": prefix,
            "entries": entries,
            "entry_count": len(entries),
            "complete": not bool(tree.get("truncated")) and len(candidates) <= MAX_TREE_ENTRIES,
        }

    def audit_fields(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"repository": result["repository"], "entry_count": result["entry_count"]}


class GitHubFileRead(_GitHubReadTool):
    """Read one bounded UTF-8-compatible text file from a public repository."""

    event_name = "github_file_read"

    def read(self, client: _PublicGitHubClient, args: dict[str, Any]) -> dict[str, Any]:
        owner, repo, metadata = client.repository(args.get("owner"), args.get("repo"))
        path = str(args.get("path") or "").strip("/")
        if not path or ".." in path.split("/") or len(path) > 1000:
            raise GitHubReadError("invalid_path", "path is invalid")
        ref = str(args.get("ref") or metadata.get("default_branch") or "").strip()
        if not REF_RE.fullmatch(ref) or ".." in ref:
            raise GitHubReadError("invalid_ref", "ref is invalid")
        content = client.get(
            f"/repos/{quote(owner)}/{quote(repo)}/contents/{quote(path, safe='/')}",
            params={"ref": ref},
        )
        if not isinstance(content, dict) or content.get("type") != "file":
            raise GitHubReadError("not_a_file", "Requested path is not a file")
        size = int(content.get("size") or 0)
        if size > MAX_FILE_BYTES:
            raise GitHubReadError("file_too_large", f"File exceeds the {MAX_FILE_BYTES}-byte limit")
        if content.get("encoding") != "base64" or not isinstance(content.get("content"), str):
            raise GitHubReadError("unsupported_encoding", "GitHub did not return base64 file content")
        try:
            raw = base64.b64decode(content["content"], validate=False)
        except (ValueError, TypeError) as exc:
            raise GitHubReadError("invalid_content", "GitHub returned invalid file content") from exc
        if len(raw) > MAX_FILE_BYTES or b"\x00" in raw:
            raise GitHubReadError("binary_or_large", "File is binary or exceeds the text limit")
        text = raw.decode("utf-8", "replace")
        return {
            "repository": f"{owner}/{repo}",
            "path": path,
            "ref": ref,
            "sha": str(content.get("sha") or "")[:100],
            "size": len(raw),
            "content": text,
        }

    def audit_fields(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"repository": result["repository"], "path": result["path"], "size": result["size"]}
