"""Bounded GitHub issue/PR workflow tools for agentic delivery.

The write surface is deliberately an enum of product-delivery operations.  It
does not expose arbitrary HTTP, GraphQL, merge, close, or review-approval
capabilities.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._runtime import append_audit
from coded_tools.colleague._runtime import atomic_write_json
from coded_tools.colleague._runtime import env_bool
from coded_tools.colleague._runtime import exclusive_file_lock
from coded_tools.colleague._runtime import json_result
from coded_tools.colleague._runtime import read_json
from coded_tools.colleague.github_project_reader import GitHubProjectReader
from coded_tools.colleague.github_project_reader import _ReaderError

API_ROOT = "https://api.github.com"
GRAPHQL_URL = f"{API_ROOT}/graphql"
NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}")
NODE_ID_RE = re.compile(r"[A-Za-z0-9_=-]{1,200}")
KEY_RE = re.compile(r"[A-Za-z0-9_.:-]{8,128}")
PROPOSAL_ID_RE = re.compile(r"[a-f0-9]{24}")
ISSUE_URL_RE = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/(\d+)")
MAX_COMMENT_CHARS = 20_000
MAX_CONTEXT_COMMENTS = 100


class GitHubDeliveryError(RuntimeError):
    """Expected failure with sanitized, user-safe text."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _csv_names(name: str, *, required: bool = True) -> tuple[str, ...]:
    values = tuple(sorted({item.strip() for item in os.getenv(name, "").split(",") if item.strip()}))
    if (required and not values) or any(not NAME_RE.fullmatch(item) for item in values):
        raise GitHubDeliveryError("invalid_config", f"{name} must contain GitHub login names")
    return values


def _repositories() -> frozenset[str]:
    values = _csv_names_like_repositories(os.getenv("GITHUB_DELIVERY_ALLOWED_REPOSITORIES", ""))
    if not values:
        raise GitHubDeliveryError(
            "invalid_config", "GITHUB_DELIVERY_ALLOWED_REPOSITORIES must contain owner/repository names"
        )
    return frozenset(value.casefold() for value in values)


def _csv_names_like_repositories(value: str) -> tuple[str, ...]:
    repositories = tuple(sorted({item.strip() for item in value.split(",") if item.strip()}))
    if any(
        len(parts := repository.split("/")) != 2 or any(not NAME_RE.fullmatch(part) for part in parts)
        for repository in repositories
    ):
        raise GitHubDeliveryError(
            "invalid_config", "GITHUB_DELIVERY_ALLOWED_REPOSITORIES must contain owner/repository names"
        )
    return repositories


def _positive_number(value: object, label: str = "number") -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise GitHubDeliveryError("invalid_number", f"{label} must be a positive integer") from exc
    if number <= 0:
        raise GitHubDeliveryError("invalid_number", f"{label} must be a positive integer")
    return number


@dataclass(frozen=True)
class _Config:
    token: str
    repositories: frozenset[str]
    pm_login: str
    coder_login: str
    reviewers: tuple[str, ...]
    timeout: float

    @classmethod
    def load(cls) -> "_Config":
        token = os.getenv("GITHUB_PM_TOKEN", "").strip()
        if not token:
            raise GitHubDeliveryError("missing_token", "GITHUB_PM_TOKEN is required")
        pm = _csv_names("GITHUB_DELIVERY_PM_LOGIN")
        coder = _csv_names("GITHUB_DELIVERY_CODER_LOGIN")
        reviewers = _csv_names("GITHUB_DELIVERY_HUMAN_REVIEWERS")
        if len(pm) != 1 or len(coder) != 1:
            raise GitHubDeliveryError(
                "invalid_config", "PM and coder identity settings must each contain exactly one GitHub login"
            )
        if {pm[0].casefold(), coder[0].casefold()} & {value.casefold() for value in reviewers}:
            raise GitHubDeliveryError("invalid_config", "Human reviewers must not include the PM or coder identity")
        try:
            timeout = float(os.getenv("GITHUB_HTTP_TIMEOUT_SECONDS", "15"))
        except ValueError as exc:
            raise GitHubDeliveryError("invalid_config", "GITHUB_HTTP_TIMEOUT_SECONDS must be numeric") from exc
        if not 1 <= timeout <= 30:
            raise GitHubDeliveryError("invalid_config", "GITHUB_HTTP_TIMEOUT_SECONDS must be between 1 and 30")
        return cls(token, _repositories(), pm[0], coder[0], reviewers, timeout)

    def repository(self, owner: object, repo: object) -> tuple[str, str]:
        owner_text, repo_text = str(owner or "").strip(), str(repo or "").strip()
        full_name = f"{owner_text}/{repo_text}"
        if (
            not NAME_RE.fullmatch(owner_text)
            or not NAME_RE.fullmatch(repo_text)
            or full_name.casefold() not in self.repositories
        ):
            raise GitHubDeliveryError("repository_not_allowed", "Repository is not in the delivery allowlist")
        return owner_text, repo_text

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "neuro-san-product-colleague",
            "X-GitHub-Api-Version": "2022-11-28",
        }


class _Client:
    def __init__(self, config: _Config):
        self.config = config

    def request(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> Any:
        try:
            response = requests.request(
                method,
                f"{API_ROOT}{path}",
                headers=self.config.headers,
                json=payload,
                timeout=(5, self.config.timeout),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise GitHubDeliveryError("request_failed", "GitHub request failed") from exc
        if response.status_code not in {200, 201}:
            raise GitHubDeliveryError("http_error", "GitHub rejected the delivery operation")
        try:
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise GitHubDeliveryError("invalid_json", "GitHub returned an invalid response") from exc

    def graphql(self, operation_name: str, query: str, variables: dict[str, Any]) -> Any:
        try:
            response = requests.post(
                GRAPHQL_URL,
                headers=self.config.headers,
                json={"operationName": operation_name, "query": query, "variables": variables},
                timeout=(5, self.config.timeout),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise GitHubDeliveryError("request_failed", "GitHub request failed") from exc
        if response.status_code != 200:
            raise GitHubDeliveryError("http_error", "GitHub rejected the delivery operation")
        try:
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise GitHubDeliveryError("invalid_json", "GitHub returned an invalid response") from exc
        if not isinstance(body, dict) or body.get("errors"):
            raise GitHubDeliveryError("graphql_error", "GitHub rejected the project status operation")
        return body


class _DeliveryTool(CodedTool):
    event_name = "github_delivery"

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del sly_data
        try:
            config = _Config.load()
            result = self.execute(_Client(config), config, args)
        except GitHubDeliveryError as exc:
            append_audit(self.event_name, ok=False, error_code=exc.code)
            return json_result(ok=False, error=exc.message)
        append_audit(self.event_name, ok=True, **self.audit_fields(result))
        return json_result(ok=True, **result)

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)

    def execute(self, client: _Client, config: _Config, args: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def audit_fields(self, result: dict[str, Any]) -> dict[str, Any]:
        return {key: result[key] for key in ("operation", "repository", "number") if key in result}


class GitHubIssueDeliveryContext(_DeliveryTool):
    """Read an issue and bounded comments from an allowlisted delivery repository."""

    event_name = "github_issue_delivery_context"

    def execute(self, client: _Client, config: _Config, args: dict[str, Any]) -> dict[str, Any]:
        owner, repo = config.repository(args.get("owner"), args.get("repo"))
        number = _positive_number(args.get("number"))
        prefix = f"/repos/{quote(owner)}/{quote(repo)}"
        issue = client.request("GET", f"{prefix}/issues/{number}")
        comments = client.request("GET", f"{prefix}/issues/{number}/comments?per_page={MAX_CONTEXT_COMMENTS}")
        if not isinstance(issue, dict) or issue.get("pull_request") or not isinstance(comments, list):
            raise GitHubDeliveryError("invalid_response", "GitHub returned an invalid issue context")
        return {
            "repository": f"{owner}/{repo}",
            "number": number,
            "title": str(issue.get("title") or "")[:500],
            "body": str(issue.get("body") or "")[:MAX_COMMENT_CHARS],
            "state": str(issue.get("state") or "")[:50],
            "assignees": _logins(issue.get("assignees")),
            "comments": _comments(comments),
            "comments_complete": len(comments) < MAX_CONTEXT_COMMENTS,
        }


class GitHubPullRequestDeliveryContext(_DeliveryTool):
    """Read a PR plus bounded conversation and review state."""

    event_name = "github_pull_request_delivery_context"

    def execute(self, client: _Client, config: _Config, args: dict[str, Any]) -> dict[str, Any]:
        owner, repo = config.repository(args.get("owner"), args.get("repo"))
        number = _positive_number(args.get("number"))
        prefix = f"/repos/{quote(owner)}/{quote(repo)}"
        pull = client.request("GET", f"{prefix}/pulls/{number}")
        conversation = client.request("GET", f"{prefix}/issues/{number}/comments?per_page={MAX_CONTEXT_COMMENTS}")
        reviews = client.request("GET", f"{prefix}/pulls/{number}/reviews?per_page={MAX_CONTEXT_COMMENTS}")
        if not isinstance(pull, dict) or not isinstance(conversation, list) or not isinstance(reviews, list):
            raise GitHubDeliveryError("invalid_response", "GitHub returned an invalid pull request context")
        return {
            "repository": f"{owner}/{repo}",
            "number": number,
            "title": str(pull.get("title") or "")[:500],
            "body": str(pull.get("body") or "")[:MAX_COMMENT_CHARS],
            "state": str(pull.get("state") or "")[:50],
            "draft": bool(pull.get("draft")),
            "merged": bool(pull.get("merged")),
            "assignees": _logins(pull.get("assignees")),
            "comments": _comments(conversation),
            "reviews": _reviews(reviews),
            "context_complete": len(conversation) < MAX_CONTEXT_COMMENTS and len(reviews) < MAX_CONTEXT_COMMENTS,
        }


class GitHubDeliveryCandidates(CodedTool):
    """Find eligible issues on exactly the environment-configured project."""

    event_name = "github_delivery_candidates"

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        # Board selection and eligibility policy are host-controlled, never model-selected.
        del args, sly_data
        try:
            reader_config = GitHubProjectReader._load_config()
            project = GitHubProjectReader._read_project(reader_config)
            config = _Config.load()
            candidates = self._eligible(project.get("items"), config.repositories)
            active_handoffs = self._active_handoffs(project.get("items"), config)
            self._attach_proposals(candidates, active_handoffs)
        except (_ReaderError, GitHubDeliveryError) as exc:
            code = exc.code
            message = exc.public_message if isinstance(exc, _ReaderError) else exc.message
            append_audit(self.event_name, ok=False, error_code=code)
            return json_result(ok=False, error=message)
        append_audit(self.event_name, ok=True, candidate_count=len(candidates))
        return json_result(
            ok=True,
            project=project.get("project"),
            candidates=candidates,
            candidate_count=len(candidates),
            active_handoffs=active_handoffs,
            active_handoff_count=len(active_handoffs),
        )

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)

    @staticmethod
    def _eligible(items: object, allowed_repositories: frozenset[str]) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            raise GitHubDeliveryError("invalid_response", "GitHub returned invalid project items")
        statuses = {
            value.strip().casefold()
            for value in os.getenv("AGENTIC_DELIVERY_ELIGIBLE_STATUSES", "Backlog,To Do").split(",")
            if value.strip()
        }
        if not statuses:
            raise GitHubDeliveryError(
                "invalid_config", "AGENTIC_DELIVERY_ELIGIBLE_STATUSES must contain at least one status"
            )
        required_label = os.getenv("AGENTIC_DELIVERY_REQUIRED_LABEL", "").strip().casefold()
        try:
            stale_days = int(os.getenv("AGENTIC_DELIVERY_STALE_AFTER_DAYS", "14"))
            max_candidates = int(os.getenv("AGENTIC_DELIVERY_MAX_CANDIDATES", "50"))
        except ValueError as exc:
            raise GitHubDeliveryError("invalid_config", "Agentic delivery limits must be integers") from exc
        if not 1 <= stale_days <= 3650 or not 1 <= max_candidates <= 200:
            raise GitHubDeliveryError("invalid_config", "Agentic delivery limits are outside safe bounds")
        stale_before = datetime.now(timezone.utc) - timedelta(days=stale_days)
        candidates: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "Issue":
                continue
            repository = str(item.get("repository") or "")
            if repository.casefold() not in allowed_repositories:
                continue
            if str(item.get("status") or "").casefold() not in statuses:
                continue
            labels = item.get("labels") if isinstance(item.get("labels"), list) else []
            if required_label and required_label not in {str(value).casefold() for value in labels}:
                continue
            assignees = item.get("assignees") if isinstance(item.get("assignees"), list) else []
            updated_at = _github_timestamp(item.get("updated_at"))
            if assignees and (updated_at is None or updated_at > stale_before):
                continue
            owner, repo = repository.split("/", 1)
            candidates.append(
                {
                    "project_item_id": str(item.get("id") or "")[:200],
                    "repository": repository,
                    "owner": owner,
                    "repo": repo,
                    "number": _positive_number(item.get("number")),
                    "title": str(item.get("title") or "")[:500],
                    "url": str(item.get("url") or "")[:1000],
                    "status": str(item.get("status") or "")[:200],
                    "assignees": [str(value)[:100] for value in assignees[:100]],
                    "labels": [str(value)[:200] for value in labels[:100]],
                    "updated_at": str(item.get("updated_at") or "")[:100],
                    "reason": "unassigned" if not assignees else "stale",
                }
            )
            if len(candidates) >= max_candidates:
                break
        return candidates

    @staticmethod
    def _active_handoffs(items: object, config: _Config) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            raise GitHubDeliveryError("invalid_response", "GitHub returned invalid project items")
        statuses = {
            value.strip().casefold()
            for value in os.getenv("AGENTIC_DELIVERY_ACTIVE_STATUSES", "In Progress,In Review").split(",")
            if value.strip()
        }
        if not statuses:
            raise GitHubDeliveryError(
                "invalid_config", "AGENTIC_DELIVERY_ACTIVE_STATUSES must contain at least one status"
            )
        try:
            maximum = int(os.getenv("AGENTIC_DELIVERY_MAX_ACTIVE_HANDOFFS", "50"))
        except ValueError as exc:
            raise GitHubDeliveryError(
                "invalid_config", "AGENTIC_DELIVERY_MAX_ACTIVE_HANDOFFS must be an integer"
            ) from exc
        if not 1 <= maximum <= 200:
            raise GitHubDeliveryError("invalid_config", "AGENTIC_DELIVERY_MAX_ACTIVE_HANDOFFS is outside safe bounds")
        automation = {config.pm_login.casefold(), config.coder_login.casefold()}
        active: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "Issue":
                continue
            repository = str(item.get("repository") or "")
            assignees = item.get("assignees") if isinstance(item.get("assignees"), list) else []
            if (
                repository.casefold() not in config.repositories
                or str(item.get("status") or "").casefold() not in statuses
                or not automation.intersection(str(value).casefold() for value in assignees)
            ):
                continue
            owner, repo = repository.split("/", 1)
            labels = item.get("labels") if isinstance(item.get("labels"), list) else []
            active.append(
                {
                    "project_item_id": str(item.get("id") or "")[:200],
                    "repository": repository,
                    "owner": owner,
                    "repo": repo,
                    "number": _positive_number(item.get("number")),
                    "title": str(item.get("title") or "")[:500],
                    "url": str(item.get("url") or "")[:1000],
                    "status": str(item.get("status") or "")[:200],
                    "assignees": [str(value)[:100] for value in assignees[:100]],
                    "labels": [str(value)[:200] for value in labels[:100]],
                    "updated_at": str(item.get("updated_at") or "")[:100],
                }
            )
            if len(active) >= maximum:
                break
        return active

    @staticmethod
    def _attach_proposals(*groups: list[dict[str, Any]]) -> None:
        path = Path(os.getenv("AGENTIC_DELIVERY_STATE_PATH", ".state/agentic_delivery.json"))
        try:
            with exclusive_file_lock(path):
                state = read_json(path, {"version": 1, "proposals": {}})
        except (OSError, ValueError) as exc:
            raise GitHubDeliveryError(
                "approval_unavailable", "Agentic delivery approval state is unavailable"
            ) from exc
        proposals = state.get("proposals") if isinstance(state, dict) else None
        if not isinstance(proposals, dict):
            raise GitHubDeliveryError("approval_unavailable", "Agentic delivery approval state is invalid")
        by_issue: dict[str, dict[str, str]] = {}
        for proposal_id, record in proposals.items():
            if not PROPOSAL_ID_RE.fullmatch(str(proposal_id)) or not isinstance(record, dict):
                continue
            issue_url = str(record.get("issue_url") or "")
            state_name = str(record.get("state") or "")
            if ISSUE_URL_RE.fullmatch(issue_url) and state_name in {"pending", "approved", "rejected", "expired"}:
                by_issue[issue_url.casefold()] = {"proposal_id": str(proposal_id), "proposal_state": state_name}
        for group in groups:
            for item in group:
                proposal = by_issue.get(str(item.get("url") or "").casefold())
                if proposal:
                    item.update(proposal)


def _github_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _logins(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted(
        {str(item.get("login") or "")[:100] for item in values[:100] if isinstance(item, dict) and item.get("login")}
    )


def _comments(values: list[Any]) -> list[dict[str, str]]:
    return [
        {
            "author": str((item.get("user") or {}).get("login") or "")[:100],
            "body": str(item.get("body") or "")[:MAX_COMMENT_CHARS],
            "created_at": str(item.get("created_at") or "")[:100],
            "url": str(item.get("html_url") or "")[:1000],
        }
        for item in values[:MAX_CONTEXT_COMMENTS]
        if isinstance(item, dict)
    ]


def _reviews(values: list[Any]) -> list[dict[str, str]]:
    return [
        {
            "author": str((item.get("user") or {}).get("login") or "")[:100],
            "state": str(item.get("state") or "")[:50],
            "body": str(item.get("body") or "")[:MAX_COMMENT_CHARS],
            "submitted_at": str(item.get("submitted_at") or "")[:100],
        }
        for item in values[:MAX_CONTEXT_COMMENTS]
        if isinstance(item, dict)
    ]


PROJECT_STATUS_MUTATION = """
mutation MoveConfiguredProjectItem($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project,
    itemId: $item,
    fieldId: $field,
    value: {singleSelectOptionId: $option}
  }) { projectV2Item { id } }
}
""".strip()

PROJECT_ITEM_QUERY = """
query ValidateConfiguredProjectItem($item: ID!) {
  node(id: $item) {
    ... on ProjectV2Item {
      id
      project { id }
      content {
        ... on Issue { number repository { nameWithOwner } }
      }
    }
  }
}
""".strip()


class GitHubDeliveryWrite(_DeliveryTool):
    """Perform one allowlisted, default-off GitHub delivery transition."""

    event_name = "github_delivery_write"
    OPERATIONS = frozenset(
        {
            "comment_issue",
            "assign_issue_to_pm",
            "assign_issue_to_coder",
            "move_issue_status",
            "comment_pr",
            "assign_pr_to_pm",
            "assign_pr_to_coder",
            "request_human_reviewers",
        }
    )

    def execute(self, client: _Client, config: _Config, args: dict[str, Any]) -> dict[str, Any]:
        if not env_bool("GITHUB_DELIVERY_WRITE_ENABLED", False):
            raise GitHubDeliveryError("writes_disabled", "GitHub delivery writes are disabled")
        owner, repo = config.repository(args.get("owner"), args.get("repo"))
        number = _positive_number(args.get("number"))
        operation = str(args.get("operation") or "").strip()
        if operation not in self.OPERATIONS:
            raise GitHubDeliveryError("operation_not_allowed", "GitHub delivery operation is not allowed")
        self._require_approval(args, owner, repo, number, operation)
        key = str(args.get("idempotency_key") or "").strip()
        if not KEY_RE.fullmatch(key):
            raise GitHubDeliveryError("invalid_idempotency_key", "idempotency_key must be 8-128 safe characters")
        fingerprint = hashlib.sha256(
            json.dumps(
                {"operation": operation, "repository": f"{owner}/{repo}".casefold(), "number": number, "args": args},
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        ledger_path = Path(os.getenv("GITHUB_DELIVERY_LEDGER_PATH", ".state/github-delivery.json"))
        with exclusive_file_lock(ledger_path):
            ledger = read_json(ledger_path, {"version": 1, "operations": {}})
            operations = ledger.get("operations")
            if not isinstance(operations, dict):
                raise GitHubDeliveryError("invalid_ledger", "GitHub delivery ledger is invalid")
            prior = operations.get(key)
            if prior:
                if not isinstance(prior, dict) or prior.get("fingerprint") != fingerprint:
                    raise GitHubDeliveryError("idempotency_conflict", "idempotency_key was already used differently")
                result = prior.get("result")
                if not isinstance(result, dict):
                    raise GitHubDeliveryError("invalid_ledger", "GitHub delivery ledger is invalid")
                return {**result, "duplicate": True}

            result = self._perform(client, config, owner, repo, number, operation, args)
            result.update({"operation": operation, "repository": f"{owner}/{repo}", "number": number})
            operations[key] = {"fingerprint": fingerprint, "result": result}
            atomic_write_json(ledger_path, ledger)
            return {**result, "duplicate": False}

    @staticmethod
    def _require_approval(args: dict[str, Any], owner: str, repo: str, number: int, operation: str) -> None:
        proposal_id = str(args.get("proposal_id") or "").strip()
        if not PROPOSAL_ID_RE.fullmatch(proposal_id):
            raise GitHubDeliveryError("approval_required", "A valid approved proposal_id is required")
        path = Path(os.getenv("AGENTIC_DELIVERY_STATE_PATH", ".state/agentic_delivery.json"))
        try:
            with exclusive_file_lock(path):
                state = read_json(path, {"version": 1, "proposals": {}})
        except (OSError, ValueError) as exc:
            raise GitHubDeliveryError(
                "approval_unavailable", "Agentic delivery approval state is unavailable"
            ) from exc
        proposals = state.get("proposals") if isinstance(state, dict) else None
        record = proposals.get(proposal_id) if isinstance(proposals, dict) else None
        if not isinstance(record, dict) or record.get("state") != "approved":
            raise GitHubDeliveryError("approval_required", "The matching coder proposal is not approved")
        match = ISSUE_URL_RE.fullmatch(str(record.get("issue_url") or ""))
        if (
            not match
            or match.group(1).casefold() != owner.casefold()
            or match.group(2).casefold() != repo.casefold()
            or int(match.group(3)) != int(record.get("issue_number") or 0)
        ):
            raise GitHubDeliveryError("approval_mismatch", "Approved proposal does not match this repository")
        approved_issue = int(match.group(3))
        if operation in {"comment_issue", "assign_issue_to_pm", "assign_issue_to_coder", "move_issue_status"}:
            if number != approved_issue:
                raise GitHubDeliveryError("approval_mismatch", "Approved proposal does not match this issue")
            return
        source_issue = _positive_number(args.get("source_issue_number"), "source_issue_number")
        if source_issue != approved_issue:
            raise GitHubDeliveryError(
                "approval_mismatch", "Approved proposal does not match this pull request workflow"
            )

    @staticmethod
    def _perform(
        client: _Client,
        config: _Config,
        owner: str,
        repo: str,
        number: int,
        operation: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        prefix = f"/repos/{quote(owner)}/{quote(repo)}"
        if operation in {"comment_issue", "comment_pr"}:
            body = str(args.get("body") or "").strip()
            if not body or len(body) > MAX_COMMENT_CHARS:
                raise GitHubDeliveryError("invalid_comment", f"body must be 1-{MAX_COMMENT_CHARS} characters")
            response = client.request("POST", f"{prefix}/issues/{number}/comments", payload={"body": body})
            return {"comment_url": str(response.get("html_url") or "")[:1000] if isinstance(response, dict) else ""}

        if operation.startswith("assign_"):
            login = config.coder_login if operation.endswith("_coder") else config.pm_login
            current = client.request("GET", f"{prefix}/issues/{number}")
            if not isinstance(current, dict):
                raise GitHubDeliveryError("invalid_response", "GitHub returned invalid assignment state")
            automation = {config.pm_login.casefold(), config.coder_login.casefold()}
            preserved = [value for value in _logins(current.get("assignees")) if value.casefold() not in automation]
            assignees = sorted({*preserved, login}, key=str.casefold)
            client.request("PATCH", f"{prefix}/issues/{number}", payload={"assignees": assignees})
            return {"assignee": login, "assignees": assignees}

        if operation == "request_human_reviewers":
            requested = args.get("reviewers")
            if not isinstance(requested, list) or not requested:
                raise GitHubDeliveryError("invalid_reviewers", "reviewers must be a non-empty list")
            normalized = tuple(sorted({str(value).strip() for value in requested if str(value).strip()}))
            allowed = {value.casefold(): value for value in config.reviewers}
            if not normalized or any(value.casefold() not in allowed for value in normalized):
                raise GitHubDeliveryError(
                    "reviewer_not_allowed", "Every reviewer must be in the human reviewer allowlist"
                )
            reviewers = [allowed[value.casefold()] for value in normalized]
            client.request("POST", f"{prefix}/pulls/{number}/requested_reviewers", payload={"reviewers": reviewers})
            return {"reviewers": reviewers}

        if operation == "move_issue_status":
            item_id = str(args.get("project_item_id") or "").strip()
            status = str(args.get("status") or "").strip()
            if not NODE_ID_RE.fullmatch(item_id):
                raise GitHubDeliveryError("invalid_project_item", "project_item_id is invalid")
            try:
                options = json.loads(os.getenv("GITHUB_PROJECT_STATUS_OPTIONS_JSON", "{}"))
            except json.JSONDecodeError as exc:
                raise GitHubDeliveryError("invalid_config", "GITHUB_PROJECT_STATUS_OPTIONS_JSON is invalid") from exc
            if (
                not isinstance(options, dict)
                or status not in options
                or not NODE_ID_RE.fullmatch(str(options[status]))
            ):
                raise GitHubDeliveryError(
                    "status_not_allowed", "status is not in the configured project status allowlist"
                )
            project = os.getenv("GITHUB_PROJECT_ID", "").strip()
            field = os.getenv("GITHUB_PROJECT_STATUS_FIELD_ID", "").strip()
            if not NODE_ID_RE.fullmatch(project) or not NODE_ID_RE.fullmatch(field):
                raise GitHubDeliveryError("invalid_config", "Configured GitHub Project IDs are missing or invalid")
            validation = client.graphql("ValidateConfiguredProjectItem", PROJECT_ITEM_QUERY, {"item": item_id})
            node = validation.get("data", {}).get("node") if isinstance(validation, dict) else None
            content = node.get("content") if isinstance(node, dict) else None
            item_project = node.get("project") if isinstance(node, dict) else None
            repository = content.get("repository") if isinstance(content, dict) else None
            if (
                not isinstance(item_project, dict)
                or item_project.get("id") != project
                or not isinstance(content, dict)
                or content.get("number") != number
                or not isinstance(repository, dict)
                or str(repository.get("nameWithOwner") or "").casefold() != f"{owner}/{repo}".casefold()
            ):
                raise GitHubDeliveryError(
                    "project_item_mismatch", "Project item does not match the configured issue and project"
                )
            client.graphql(
                "MoveConfiguredProjectItem",
                PROJECT_STATUS_MUTATION,
                {"project": project, "item": item_id, "field": field, "option": str(options[status])},
            )
            return {"project_item_id": item_id, "status": status}

        raise GitHubDeliveryError("operation_not_allowed", "GitHub delivery operation is not allowed")
