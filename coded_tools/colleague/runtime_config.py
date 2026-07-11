"""Expose non-secret runtime configuration to the agent network."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.colleague._runtime import json_result
from coded_tools.colleague._runtime import read_env_bool


class RuntimeConfig(CodedTool):
    """Return safe configuration and readiness without ever returning credentials."""

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        del args, sly_data
        owner = os.getenv("GITHUB_PROJECT_OWNER", "").strip()
        owner_type = os.getenv("GITHUB_PROJECT_OWNER_TYPE", "org").strip().lower()
        project_number_raw = os.getenv("GITHUB_PROJECT_NUMBER", "").strip()
        channel_id = os.getenv("SLACK_CHANNEL_ID", "").strip()
        allowed_users = sorted(
            user.strip() for user in os.getenv("SLACK_ALLOWED_USER_IDS", "").split(",") if user.strip()
        )

        missing: list[str] = []
        for name in ("GITHUB_TOKEN", "GITHUB_PROJECT_OWNER", "GITHUB_PROJECT_NUMBER"):
            if not os.getenv(name, "").strip():
                missing.append(name)
        if owner_type not in {"org", "user"}:
            missing.append("GITHUB_PROJECT_OWNER_TYPE must be org or user")

        try:
            project_number = int(project_number_raw) if project_number_raw else None
        except ValueError:
            project_number = None
            missing.append("GITHUB_PROJECT_NUMBER must be a positive integer")
        if project_number is not None and project_number <= 0:
            project_number = None
            missing.append("GITHUB_PROJECT_NUMBER must be a positive integer")

        slack_missing = [
            name
            for name in (
                "SLACK_BOT_TOKEN",
                "SLACK_BOT_USER_ID",
                "SLACK_CHANNEL_ID",
                "SLACK_ALLOWED_USER_IDS",
            )
            if not os.getenv(name, "").strip()
        ]

        write_enabled, write_error = read_env_bool("COLLEAGUE_SLACK_WRITE_ENABLED", False)
        require_mention, mention_error = read_env_bool("COLLEAGUE_SLACK_REQUIRE_MENTION", True)
        gmail_enabled, gmail_enabled_error = read_env_bool("COLLEAGUE_GMAIL_ENABLED", False)
        gmail_write_enabled, gmail_write_error = read_env_bool("COLLEAGUE_GMAIL_WRITE_ENABLED", False)
        gmail_token_path = Path(os.getenv("GMAIL_TOKEN_PATH", ".secrets/gmail-token.json"))
        gmail_allowed = {
            value.strip().lower()
            for value in os.getenv("GMAIL_ALLOWED_RECIPIENTS", "").split(",")
            if value.strip()
        }

        max_run_seconds, max_run_error = self._safe_positive_int("COLLEAGUE_MAX_RUN_SECONDS", 600)
        report_interval_hours, report_error = self._safe_positive_int("COLLEAGUE_REPORT_INTERVAL_HOURS", 24)
        stale_after_days, stale_error = self._safe_positive_int("COLLEAGUE_STALE_AFTER_DAYS", 14)
        max_project_items, max_items_error = self._safe_bounded_int("COLLEAGUE_MAX_PROJECT_ITEMS", 500, 1000)
        slack_max_pages, slack_pages_error = self._safe_bounded_int("COLLEAGUE_SLACK_MAX_PAGES", 10, 100)
        slack_max_requests, slack_requests_error = self._safe_bounded_int(
            "COLLEAGUE_SLACK_MAX_REQUESTS", 50, 500
        )
        slack_max_thread_pages, slack_thread_pages_error = self._safe_bounded_int(
            "COLLEAGUE_SLACK_MAX_THREAD_PAGES", 10, 100
        )
        slack_initial_lookback_hours, slack_lookback_error = self._safe_bounded_int(
            "COLLEAGUE_SLACK_INITIAL_LOOKBACK_HOURS", 24, 720
        )
        slack_event_max_attempts, slack_attempts_error = self._safe_bounded_int(
            "COLLEAGUE_SLACK_EVENT_MAX_ATTEMPTS", 3, 10
        )
        missing.extend(
            error
            for error in (
                write_error,
                mention_error,
                max_run_error,
                report_error,
                stale_error,
                max_items_error,
                slack_pages_error,
                slack_requests_error,
                slack_thread_pages_error,
                slack_lookback_error,
                slack_attempts_error,
                gmail_enabled_error,
                gmail_write_error,
            )
            if error
        )
        if max_run_seconds != 600:
            missing.append("COLLEAGUE_MAX_RUN_SECONDS must be 600 to match the agent timeout")
        if os.getenv("AGENT_REQUEST_LOGGING_INPUT_SLICE") != "0":
            missing.append("AGENT_REQUEST_LOGGING_INPUT_SLICE must be 0")
        all_problems = sorted(set(missing + slack_missing))
        if gmail_enabled and not gmail_token_path.is_file():
            all_problems.append("GMAIL_TOKEN_PATH must point to an authorized token file when Gmail is enabled")
        if gmail_write_enabled and not gmail_enabled:
            all_problems.append("COLLEAGUE_GMAIL_ENABLED must be true before Gmail writes can be enabled")
        if gmail_write_enabled and not gmail_allowed:
            all_problems.append("GMAIL_ALLOWED_RECIPIENTS is required when Gmail writes are enabled")
        all_problems = sorted(set(all_problems))

        return json_result(
            ok=not all_problems,
            github={
                "owner": owner,
                "owner_type": owner_type,
                "project_number": project_number,
                "mcp_read_only": True,
            },
            slack={
                "channel_id": channel_id,
                "allowed_user_count": len(allowed_users),
                "read_ready": not slack_missing and mention_error is None,
                "require_mention": require_mention,
                "write_enabled": write_enabled,
            },
            gmail={
                "enabled": gmail_enabled,
                "read_ready": gmail_enabled and gmail_token_path.is_file(),
                "write_enabled": gmail_write_enabled,
                "allowed_recipient_count": len(gmail_allowed),
                "query_prefix_configured": bool(os.getenv("GMAIL_QUERY_PREFIX", "in:inbox newer_than:30d").strip()),
            },
            policy={
                "max_run_seconds": max_run_seconds,
                "report_interval_hours": report_interval_hours,
                "stale_after_days": stale_after_days,
                "max_project_items": max_project_items,
                "slack_max_pages": slack_max_pages,
                "slack_max_requests": slack_max_requests,
                "slack_max_thread_pages": slack_max_thread_pages,
                "slack_initial_lookback_hours": slack_initial_lookback_hours,
                "slack_event_max_attempts": slack_event_max_attempts,
            },
            missing=all_problems,
            note="Secret values are intentionally never returned.",
        )

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.invoke, args, sly_data)

    @staticmethod
    def _safe_positive_int(name: str, default: int) -> tuple[int, str | None]:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError:
            return default, f"{name} must be a positive integer"
        if value <= 0:
            return default, f"{name} must be a positive integer"
        return value, None

    @staticmethod
    def _safe_bounded_int(name: str, default: int, maximum: int) -> tuple[int, str | None]:
        value, error = RuntimeConfig._safe_positive_int(name, default)
        if error:
            return value, error
        if value > maximum:
            return default, f"{name} must be no greater than {maximum}"
        return value, None
