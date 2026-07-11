"""Read one environment-configured GitHub Project through a fixed GraphQL query."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any

import requests
from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._runtime import append_audit
from coded_tools.colleague._runtime import json_result

GRAPHQL_URL = "https://api.github.com/graphql"
PAGE_SIZE = 50
MAX_PROJECT_ITEMS = 1000
MAX_ITEM_VALUES = 100
MAX_CURSOR_LENGTH = 2048
CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 15.0
MAX_READ_TIMEOUT_SECONDS = 30.0
OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,98}[A-Za-z0-9])?")

PROJECT_QUERY_TEMPLATE = """
query ReadConfiguredProject($owner: String!, $number: Int!, $cursor: String, $pageSize: Int!) {
  __OWNER_FIELD__(login: $owner) {
    projectV2(number: $number) {
      id
      number
      title
      url
      items(first: $pageSize, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          updatedAt
          status: fieldValueByName(name: "Status") {
            __typename
            ... on ProjectV2ItemFieldSingleSelectValue { name }
            ... on ProjectV2ItemFieldTextValue { text }
            ... on ProjectV2ItemFieldNumberValue { number }
          }
          priority: fieldValueByName(name: "Priority") {
            __typename
            ... on ProjectV2ItemFieldSingleSelectValue { name }
            ... on ProjectV2ItemFieldTextValue { text }
            ... on ProjectV2ItemFieldNumberValue { number }
          }
          content {
            __typename
            ... on Issue {
              id
              number
              title
              url
              updatedAt
              assignees(first: 100) { totalCount nodes { login } }
              labels(first: 100) { totalCount nodes { name } }
            }
            ... on PullRequest {
              id
              number
              title
              url
              updatedAt
              assignees(first: 100) { totalCount nodes { login } }
              labels(first: 100) { totalCount nodes { name } }
            }
            ... on DraftIssue {
              id
              title
              updatedAt
              assignees(first: 100) { totalCount nodes { login } }
            }
          }
        }
      }
    }
  }
}
""".strip()


class _ReaderError(RuntimeError):
    """Expected, sanitized reader failure."""

    def __init__(self, code: str, public_message: str):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


@dataclass(frozen=True)
class _ReaderConfig:
    token: str
    owner: str
    owner_type: str
    owner_field: str
    project_number: int
    max_items: int
    read_timeout: float


class GitHubProjectReader(CodedTool):
    """Return normalized items from exactly one host-configured GitHub Project."""

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        # Resource selection is intentionally unavailable to the model.
        del args, sly_data
        try:
            config = self._load_config()
            result = self._read_project(config)
        except _ReaderError as exc:
            append_audit("github_project_reader", ok=False, error_code=exc.code)
            return json_result(ok=False, error=exc.public_message)
        append_audit("github_project_reader", ok=True, item_count=result["item_count"])
        return json_result(ok=True, **result)

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)

    @staticmethod
    def _load_config() -> _ReaderConfig:
        token = os.getenv("GITHUB_TOKEN", "").strip()
        owner = os.getenv("GITHUB_PROJECT_OWNER", "").strip()
        owner_type = os.getenv("GITHUB_PROJECT_OWNER_TYPE", "org").strip().lower()
        project_number_raw = os.getenv("GITHUB_PROJECT_NUMBER", "").strip()
        if not token:
            raise _ReaderError("missing_token", "GITHUB_TOKEN is required")
        if not owner or not OWNER_RE.fullmatch(owner):
            raise _ReaderError("invalid_owner", "GITHUB_PROJECT_OWNER is invalid")
        if owner_type not in {"org", "user"}:
            raise _ReaderError("invalid_owner_type", "GITHUB_PROJECT_OWNER_TYPE must be org or user")
        try:
            project_number = int(project_number_raw)
        except ValueError as exc:
            raise _ReaderError(
                "invalid_project_number", "GITHUB_PROJECT_NUMBER must be a positive integer"
            ) from exc
        if project_number <= 0:
            raise _ReaderError("invalid_project_number", "GITHUB_PROJECT_NUMBER must be a positive integer")
        max_items = GitHubProjectReader._bounded_int(
            "COLLEAGUE_MAX_PROJECT_ITEMS", default=500, maximum=MAX_PROJECT_ITEMS
        )
        read_timeout = GitHubProjectReader._bounded_float(
            "GITHUB_HTTP_TIMEOUT_SECONDS",
            default=DEFAULT_READ_TIMEOUT_SECONDS,
            maximum=MAX_READ_TIMEOUT_SECONDS,
        )
        return _ReaderConfig(
            token=token,
            owner=owner,
            owner_type=owner_type,
            owner_field="organization" if owner_type == "org" else "user",
            project_number=project_number,
            max_items=max_items,
            read_timeout=read_timeout,
        )

    @staticmethod
    def _bounded_int(name: str, *, default: int, maximum: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError as exc:
            raise _ReaderError("invalid_limit", f"{name} must be a positive integer") from exc
        if value < 1 or value > maximum:
            raise _ReaderError("invalid_limit", f"{name} must be between 1 and {maximum}")
        return value

    @staticmethod
    def _bounded_float(name: str, *, default: float, maximum: float) -> float:
        try:
            value = float(os.getenv(name, str(default)))
        except ValueError as exc:
            raise _ReaderError("invalid_timeout", f"{name} must be numeric") from exc
        if value < 1 or value > maximum:
            raise _ReaderError("invalid_timeout", f"{name} must be between 1 and {maximum:g} seconds")
        return value

    @classmethod
    def _read_project(cls, config: _ReaderConfig) -> dict[str, Any]:
        query = PROJECT_QUERY_TEMPLATE.replace("__OWNER_FIELD__", config.owner_field)
        page_size = min(PAGE_SIZE, config.max_items)
        max_pages = (config.max_items + page_size - 1) // page_size
        cursor: str | None = None
        seen_cursors: set[str] = set()
        items: list[dict[str, Any]] = []
        project_summary: dict[str, Any] | None = None
        project_id: str | None = None

        for _page in range(max_pages):
            variables = {
                "owner": config.owner,
                "number": config.project_number,
                "cursor": cursor,
                "pageSize": page_size,
            }
            body = cls._post_query(query, variables, config)
            project = cls._extract_project(body, config.owner_field)
            current_project_id = cls._bounded_string(project.get("id"), 200)
            if not current_project_id:
                raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid project response")
            if project_id is not None and current_project_id != project_id:
                raise _ReaderError("project_changed", "The configured GitHub Project changed during pagination")
            project_id = current_project_id
            if project_summary is None:
                project_summary = {
                    "owner": config.owner,
                    "owner_type": config.owner_type,
                    "number": config.project_number,
                    "title": cls._bounded_string(project.get("title"), 300),
                    "url": cls._bounded_string(project.get("url"), 1000),
                }

            connection = project.get("items")
            if not isinstance(connection, dict):
                raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid items response")
            nodes = connection.get("nodes")
            page_info = connection.get("pageInfo")
            if not isinstance(nodes, list) or not isinstance(page_info, dict):
                raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid items response")
            for node in nodes:
                if not isinstance(node, dict):
                    raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid project item")
                items.append(cls._normalize_item(node))
                if len(items) > config.max_items:
                    raise _ReaderError(
                        "item_limit", f"The configured GitHub Project exceeds the {config.max_items} item limit"
                    )

            has_next_page = page_info.get("hasNextPage")
            if not isinstance(has_next_page, bool):
                raise _ReaderError("invalid_response", "GitHub GraphQL returned invalid pagination metadata")
            if not has_next_page:
                if project_summary is None:  # pragma: no cover - guarded by successful extraction
                    raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid project response")
                return {
                    "project": project_summary,
                    "items": items,
                    "item_count": len(items),
                    "complete": True,
                }
            if len(items) >= config.max_items:
                raise _ReaderError(
                    "item_limit", f"The configured GitHub Project exceeds the {config.max_items} item limit"
                )
            next_cursor = page_info.get("endCursor")
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or len(next_cursor) > MAX_CURSOR_LENGTH
                or next_cursor in seen_cursors
            ):
                raise _ReaderError("invalid_cursor", "GitHub GraphQL returned an invalid pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        raise _ReaderError("page_limit", "GitHub GraphQL pagination exceeded the configured safety limit")

    @staticmethod
    def _post_query(query: str, variables: dict[str, Any], config: _ReaderConfig) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {config.token}",
            "User-Agent": "neuro-san-team-colleague",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            response = requests.post(
                GRAPHQL_URL,
                headers=headers,
                json={"operationName": "ReadConfiguredProject", "query": query, "variables": variables},
                timeout=(CONNECT_TIMEOUT_SECONDS, config.read_timeout),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise _ReaderError("request_failed", "GitHub GraphQL request failed") from exc
        if response.status_code != 200:
            raise _ReaderError("http_error", "GitHub GraphQL request was rejected")
        try:
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise _ReaderError("invalid_json", "GitHub GraphQL returned an invalid response") from exc
        if not isinstance(body, dict):
            raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid response")
        if body.get("errors"):
            raise _ReaderError("graphql_error", "GitHub GraphQL rejected the configured project query")
        return body

    @staticmethod
    def _extract_project(body: dict[str, Any], owner_field: str) -> dict[str, Any]:
        data = body.get("data")
        if not isinstance(data, dict):
            raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid response")
        owner = data.get(owner_field)
        if not isinstance(owner, dict):
            raise _ReaderError("project_not_found", "The configured GitHub Project was not found or is inaccessible")
        project = owner.get("projectV2")
        if not isinstance(project, dict):
            raise _ReaderError("project_not_found", "The configured GitHub Project was not found or is inaccessible")
        return project

    @classmethod
    def _normalize_item(cls, node: dict[str, Any]) -> dict[str, Any]:
        content = node.get("content")
        if content is not None and not isinstance(content, dict):
            raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid project item")
        content = content or {}
        typename = cls._bounded_string(content.get("__typename"), 100) or "Redacted"
        type_name = {"PullRequest": "PullRequest", "DraftIssue": "DraftIssue", "Issue": "Issue"}.get(
            typename, "Redacted"
        )
        assignees = cls._connection_names(content.get("assignees"), "login")
        labels = cls._connection_names(content.get("labels"), "name")
        status = cls._field_value(node.get("status")) or "No status"
        priority = cls._field_value(node.get("priority"))
        return {
            "id": cls._bounded_string(node.get("id") or content.get("id"), 1000),
            "type": type_name,
            "number": cls._bounded_string(content.get("number"), 100),
            "title": cls._bounded_string(content.get("title"), 500),
            "url": cls._bounded_string(content.get("url"), 1000),
            "status": status[:200],
            "priority": priority[:200],
            "assignees": assignees,
            "labels": labels,
            "updated_at": cls._bounded_string(content.get("updatedAt") or node.get("updatedAt"), 100),
        }

    @staticmethod
    def _connection_names(value: Any, key: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, dict):
            raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid project item")
        nodes = value.get("nodes")
        total_count = value.get("totalCount")
        if not isinstance(nodes, list) or not isinstance(total_count, int) or total_count < 0:
            raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid project item")
        if total_count > MAX_ITEM_VALUES or total_count > len(nodes):
            raise _ReaderError("nested_limit", "A GitHub Project item exceeds a nested value safety limit")
        names: set[str] = set()
        for node in nodes:
            if not isinstance(node, dict):
                raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid project item")
            name = GitHubProjectReader._bounded_string(node.get(key), 200)
            if name:
                names.add(name)
        return sorted(names)

    @staticmethod
    def _field_value(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, dict):
            raise _ReaderError("invalid_response", "GitHub GraphQL returned an invalid project field value")
        for key in ("name", "text", "number"):
            field_value = value.get(key)
            if field_value is not None:
                return GitHubProjectReader._bounded_string(field_value, 200)
        return ""

    @staticmethod
    def _bounded_string(value: Any, limit: int) -> str:
        if value is None:
            return ""
        return str(value)[:limit]
