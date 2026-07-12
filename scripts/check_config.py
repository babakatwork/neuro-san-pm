"""Fail-closed configuration and runtime contract check."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from pathlib import Path

from croniter import croniter
from dotenv import load_dotenv
from packaging.version import Version
from pyhocon import ConfigFactory

ROOT = Path(__file__).resolve().parents[1]
TRUE_ENV_VALUES = frozenset({"1", "true", "t", "yes", "y", "on"})
FALSE_ENV_VALUES = frozenset({"0", "false", "f", "no", "n", "off"})


def read_env_bool(name: str, default: bool = False) -> tuple[bool, str | None]:
    """Parse an environment boolean without allowing typos to enable it."""
    value = os.getenv(name)
    if value is None:
        return default, None
    normalized = value.strip().lower()
    if normalized in TRUE_ENV_VALUES:
        return True, None
    if normalized in FALSE_ENV_VALUES:
        return False, None
    return default, f"{name} must be a recognized boolean (true/false)"


def read_positive_int(name: str, default: int) -> tuple[int, str | None]:
    """Parse a strictly positive integer environment setting."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default, f"{name} must be a positive integer"
    if value <= 0:
        return default, f"{name} must be a positive integer"
    return value, None


def read_bounded_int(name: str, default: int, maximum: int) -> tuple[int, str | None]:
    """Parse a positive integer with a host-enforced upper bound."""
    value, error = read_positive_int(name, default)
    if error:
        return value, error
    if value > maximum:
        return default, f"{name} must be no greater than {maximum}"
    return value, None


def main() -> int:
    """Validate configuration without making any external calls."""
    load_dotenv(ROOT / ".env")
    errors: list[str] = []
    warnings: list[str] = []

    required = [
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "GITHUB_PROJECT_OWNER",
        "GITHUB_PROJECT_NUMBER",
        "SLACK_BOT_TOKEN",
        "SLACK_BOT_USER_ID",
        "SLACK_CHANNEL_ID",
        "SLACK_ALLOWED_USER_IDS",
    ]
    errors.extend(f"missing {name}" for name in required if not os.getenv(name, "").strip())
    if os.getenv("GITHUB_PROJECT_OWNER_TYPE", "org") not in {"org", "user"}:
        errors.append("GITHUB_PROJECT_OWNER_TYPE must be org or user")
    try:
        project_number = int(os.getenv("GITHUB_PROJECT_NUMBER", ""))
        if project_number <= 0:
            raise ValueError
    except ValueError:
        errors.append("GITHUB_PROJECT_NUMBER must be a positive integer")
    if os.getenv("AGENT_HTTP_SERVER_INSTANCES", "1") != "1":
        errors.append("AGENT_HTTP_SERVER_INSTANCES must be 1 to avoid duplicate schedulers")
    if os.getenv("NEURO_SAN_SERVER_HTTP_PORT", "8080") != "8080":
        errors.append("NEURO_SAN_SERVER_HTTP_PORT must be 8080 for this deployment contract")
    if os.getenv("AGENT_REQUEST_LOGGING_INPUT_SLICE") != "0":
        errors.append("AGENT_REQUEST_LOGGING_INPUT_SLICE must be 0 to redact request text")

    write_enabled, write_error = read_env_bool("COLLEAGUE_SLACK_WRITE_ENABLED", False)
    _, mention_error = read_env_bool("COLLEAGUE_SLACK_REQUIRE_MENTION", True)
    errors.extend(error for error in (write_error, mention_error) if error)

    cron = os.getenv("COLLEAGUE_CRON_SCHEDULE", "*/15 * * * *")
    max_run, max_run_error = read_positive_int("COLLEAGUE_MAX_RUN_SECONDS", 600)
    _, report_error = read_positive_int("COLLEAGUE_REPORT_INTERVAL_HOURS", 24)
    _, stale_error = read_positive_int("COLLEAGUE_STALE_AFTER_DAYS", 14)
    _, max_items_error = read_bounded_int("COLLEAGUE_MAX_PROJECT_ITEMS", 500, 1000)
    _, slack_pages_error = read_bounded_int("COLLEAGUE_SLACK_MAX_PAGES", 10, 100)
    _, slack_requests_error = read_bounded_int("COLLEAGUE_SLACK_MAX_REQUESTS", 50, 500)
    _, slack_thread_pages_error = read_bounded_int("COLLEAGUE_SLACK_MAX_THREAD_PAGES", 10, 100)
    _, slack_lookback_error = read_bounded_int("COLLEAGUE_SLACK_INITIAL_LOOKBACK_HOURS", 24, 720)
    _, slack_attempts_error = read_bounded_int("COLLEAGUE_SLACK_EVENT_MAX_ATTEMPTS", 3, 10)
    errors.extend(
        error
        for error in (
            max_run_error,
            report_error,
            stale_error,
            max_items_error,
            slack_pages_error,
            slack_requests_error,
            slack_thread_pages_error,
            slack_lookback_error,
            slack_attempts_error,
        )
        if error
    )
    if max_run != 600:
        errors.append("COLLEAGUE_MAX_RUN_SECONDS must be 600 to match the agent timeout")
    try:
        iterator = croniter(cron, datetime.now())
        first = iterator.get_next(datetime)
        second = iterator.get_next(datetime)
        interval = (second - first).total_seconds()
        if interval <= max_run:
            errors.append("COLLEAGUE_CRON_SCHEDULE interval must exceed COLLEAGUE_MAX_RUN_SECONDS")
    except (TypeError, ValueError) as exc:
        errors.append(f"invalid COLLEAGUE_CRON_SCHEDULE: {exc}")

    try:
        core_version = Version(version("neuro-san"))
        if core_version != Version("0.6.76"):
            errors.append(f"neuro-san must be 0.6.76, found {core_version}")
    except PackageNotFoundError as exc:
        errors.append(f"dependency not installed: {exc.name}")

    try:
        ConfigFactory.parse_file(ROOT / "registries" / "manifest.hocon")
        network_path = ROOT / "registries" / "product_colleague.hocon"
        ConfigFactory.parse_string(network_path.read_text(encoding="utf-8"), basedir=ROOT)
        ConfigFactory.parse_file(ROOT / "mcp" / "mcp_info.hocon")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        errors.append(f"HOCON parse failed: {type(exc).__name__}: {exc}")

    if not write_enabled:
        warnings.append("Slack posting is in dry-run mode (recommended for the first run)")

    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Configuration contract passed; no external service was contacted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
