"""Host-enforced fork-only workspace and pull-request boundary."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._runtime import append_audit
from coded_tools.colleague._runtime import json_result

API_ROOT = "https://api.github.com"
NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}")
PR_URL_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)"
)
GITHUB_REPOSITORY_RE = re.compile(
    r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


class ForkBoundaryError(RuntimeError):
    """Expected failure with a secret-free public message."""


def _repositories() -> frozenset[str]:
    values = {
        value.strip().casefold()
        for value in os.getenv("GITHUB_DELIVERY_ALLOWED_REPOSITORIES", "").split(",")
        if value.strip()
    }
    if not values or any(value.count("/") != 1 for value in values):
        raise ForkBoundaryError("GITHUB_DELIVERY_ALLOWED_REPOSITORIES is invalid")
    return frozenset(values)


def _repository(owner: object, repo: object) -> tuple[str, str]:
    owner_text, repo_text = str(owner or "").strip(), str(repo or "").strip()
    if (
        not NAME_RE.fullmatch(owner_text)
        or not NAME_RE.fullmatch(repo_text)
        or f"{owner_text}/{repo_text}".casefold() not in _repositories()
    ):
        raise ForkBoundaryError("Repository is not in the delivery allowlist")
    return owner_text, repo_text


def _workspace(value: object) -> Path:
    requested = Path(str(value or "")).expanduser().resolve()
    primary = Path(os.getenv("CODING_AGENT_PRIMARY_WORKSPACE", "")).expanduser().resolve()
    roots = [
        Path(item).expanduser().resolve()
        for item in os.getenv("CODING_AGENT_ALLOWED_WORKSPACES", "").split(os.pathsep)
        if item.strip()
    ]
    if (
        requested != primary
        or not requested.is_dir()
        or not (requested / ".git").exists()
        or not any(requested == root or requested.is_relative_to(root) for root in roots)
    ):
        raise ForkBoundaryError("Workspace is not the configured Git repository")
    return requested


def _git(workspace: Path, *args: str, check: bool = True) -> str:
    env = os.environ.copy()
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PM_TOKEN", "GITHUB_CODER_TOKEN"):
        env.pop(name, None)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    if check and result.returncode != 0:
        raise ForkBoundaryError("Local Git remote configuration failed")
    return result.stdout.strip()


def _remote_repository(value: str) -> str | None:
    match = GITHUB_REPOSITORY_RE.fullmatch(value.strip())
    return f"{match.group(1)}/{match.group(2)}".casefold() if match else None


def _github_repository(token: str, owner: str, repo: str) -> dict[str, Any]:
    if not token:
        raise ForkBoundaryError("Separate PM and coder GitHub tokens are required")
    response = requests.get(
        f"{API_ROOT}/repos/{quote(owner)}/{quote(repo)}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "neuro-san-pm/fork-boundary",
        },
        timeout=15,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise ForkBoundaryError("GitHub could not verify fork permissions")
    value = response.json()
    if not isinstance(value, dict):
        raise ForkBoundaryError("GitHub returned invalid fork permissions")
    return value


def _can_push(repository: dict[str, Any]) -> bool:
    permissions = repository.get("permissions")
    return bool(isinstance(permissions, dict) and permissions.get("push"))


class CoderForkBoundary(CodedTool):
    """Prepare one fork-only workspace or verify one cross-fork PR."""

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del sly_data
        operation = str(args.get("operation") or "").strip()
        try:
            if operation == "prepare_workspace":
                result = self._prepare(args)
            elif operation == "verify_pull_request":
                result = self._verify_pull_request(args)
            else:
                raise ForkBoundaryError("operation must be prepare_workspace or verify_pull_request")
            append_audit("coder_fork_boundary", ok=True, operation=operation)
            return json_result(ok=True, **result)
        except (
            ForkBoundaryError,
            OSError,
            ValueError,
            requests.RequestException,
            subprocess.SubprocessError,
        ) as exc:
            message = str(exc) if isinstance(exc, ForkBoundaryError) else "Fork boundary is unavailable"
            append_audit("coder_fork_boundary", ok=False, operation=operation, error=message)
            return json_result(ok=False, error=message)

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)

    @staticmethod
    def _prepare(args: dict[str, Any]) -> dict[str, Any]:
        owner, repo = _repository(args.get("owner"), args.get("repo"))
        workspace = _workspace(args.get("workspace"))
        coder = os.getenv("GITHUB_DELIVERY_CODER_LOGIN", "").strip()
        if not NAME_RE.fullmatch(coder) or coder.casefold() == owner.casefold():
            raise ForkBoundaryError("Configured coder fork owner is invalid")

        upstream_name = f"{owner}/{repo}".casefold()
        fork_name = f"{coder}/{repo}".casefold()
        pm_token = os.getenv("GITHUB_PM_TOKEN", "").strip()
        coder_token = os.getenv("GITHUB_CODER_TOKEN", "").strip()
        if not pm_token or not coder_token or pm_token == coder_token:
            raise ForkBoundaryError("Separate PM and coder GitHub tokens are required")
        upstream_as_pm = _github_repository(pm_token, owner, repo)
        upstream_as_coder = _github_repository(coder_token, owner, repo)
        fork_as_coder = _github_repository(coder_token, coder, repo)
        parent = fork_as_coder.get("parent")
        if _can_push(upstream_as_pm) or _can_push(upstream_as_coder):
            raise ForkBoundaryError("PM and coder must not have upstream push permission")
        if (
            not _can_push(fork_as_coder)
            or not bool(fork_as_coder.get("fork"))
            or not isinstance(parent, dict)
            or str(parent.get("full_name") or "").casefold() != upstream_name
        ):
            raise ForkBoundaryError("Coder must have push permission only on its verified fork")

        existing = {
            _remote_repository(_git(workspace, "remote", "get-url", remote, check=False))
            for remote in ("origin", "upstream")
        }
        if upstream_name not in existing and fork_name not in existing:
            raise ForkBoundaryError("Workspace does not belong to the configured upstream or coder fork")

        fork_url = f"https://github.com/{coder}/{repo}.git"
        upstream_url = f"https://github.com/{owner}/{repo}.git"
        if _git(workspace, "remote", "get-url", "origin", check=False):
            _git(workspace, "remote", "set-url", "origin", fork_url)
        else:
            _git(workspace, "remote", "add", "origin", fork_url)
        if _git(workspace, "remote", "get-url", "upstream", check=False):
            _git(workspace, "remote", "set-url", "upstream", upstream_url)
        else:
            _git(workspace, "remote", "add", "upstream", upstream_url)

        return {
            "workspace": str(workspace),
            "origin_repository": f"{coder}/{repo}",
            "upstream_repository": f"{owner}/{repo}",
            "policy": "Push only to origin; open a PR into the upstream default branch; never merge.",
        }

    @staticmethod
    def _verify_pull_request(args: dict[str, Any]) -> dict[str, Any]:
        owner, repo = _repository(args.get("owner"), args.get("repo"))
        pr_url = str(args.get("pr_url") or "").strip()
        match = PR_URL_RE.fullmatch(pr_url)
        if (
            not match
            or match.group(1).casefold() != owner.casefold()
            or match.group(2).casefold() != repo.casefold()
        ):
            raise ForkBoundaryError("Pull request must target the configured upstream repository")
        token = os.getenv("GITHUB_PM_TOKEN", "").strip()
        if not token:
            raise ForkBoundaryError("GITHUB_PM_TOKEN is required")
        coder = os.getenv("GITHUB_DELIVERY_CODER_LOGIN", "").strip()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "neuro-san-pm/fork-boundary",
        }
        timeout = 15
        pull = requests.get(
            f"{API_ROOT}/repos/{quote(owner)}/{quote(repo)}/pulls/{int(match.group(3))}",
            headers=headers, timeout=timeout, allow_redirects=False
        )
        fork = requests.get(
            f"{API_ROOT}/repos/{quote(coder)}/{quote(repo)}",
            headers=headers, timeout=timeout, allow_redirects=False
        )
        if pull.status_code != 200 or fork.status_code != 200:
            raise ForkBoundaryError("GitHub could not verify the fork pull request")
        pull_data, fork_data = pull.json(), fork.json()
        base_repo = pull_data.get("base", {}).get("repo", {})
        head_repo = pull_data.get("head", {}).get("repo", {})
        parent = fork_data.get("parent", {})
        expected_upstream = f"{owner}/{repo}"
        expected_fork = f"{coder}/{repo}"
        if (
            str(base_repo.get("full_name") or "").casefold() != expected_upstream.casefold()
            or str(head_repo.get("full_name") or "").casefold() != expected_fork.casefold()
            or str(parent.get("full_name") or "").casefold() != expected_upstream.casefold()
            or not bool(fork_data.get("fork"))
            or pull_data.get("state") != "open"
            or bool(pull_data.get("merged"))
            or str(pull_data.get("base", {}).get("ref") or "")
            != str(base_repo.get("default_branch") or "")
            or not str(pull_data.get("head", {}).get("ref") or "").strip()
        ):
            raise ForkBoundaryError("Pull request violates the configured fork-only boundary")
        return {
            "verified": True,
            "pr_url": pr_url,
            "upstream_repository": expected_upstream,
            "fork_repository": expected_fork,
            "base_branch": str(pull_data["base"]["ref"]),
            "head_branch": str(pull_data["head"]["ref"]),
            "merged": False,
        }
