"""Fail-closed configuration and runtime contract check."""

from __future__ import annotations

import json
import os
import re
import shutil
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from coded_tools.colleague.github_public_read import GitHubReadError  # noqa: E402
from coded_tools.colleague.github_public_read import allowed_repository_names  # noqa: E402
from coded_tools.colleague.gmail_recipients import validate_daily_summary_recipients  # noqa: E402

TRUE_ENV_VALUES = frozenset({"1", "true", "t", "yes", "y", "on"})
FALSE_ENV_VALUES = frozenset({"0", "false", "f", "no", "n", "off"})
GITHUB_LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")


def comma_values(name: str) -> list[str]:
    """Return non-empty comma-separated configuration values."""
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


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
    try:
        allowed_repository_names()
    except GitHubReadError as exc:
        errors.append(exc.message)
    if os.getenv("AGENT_HTTP_SERVER_INSTANCES", "1") != "1":
        errors.append("AGENT_HTTP_SERVER_INSTANCES must be 1 to avoid duplicate schedulers")
    try:
        server_port = int(os.getenv("NEURO_SAN_SERVER_HTTP_PORT", "8188"))
        if not 1024 <= server_port <= 65535:
            raise ValueError
        if server_port == 8080:
            errors.append("NEURO_SAN_SERVER_HTTP_PORT must not use Neuro SAN's default port 8080")
    except ValueError:
        errors.append("NEURO_SAN_SERVER_HTTP_PORT must be between 1024 and 65535")
    if os.getenv("AGENT_REQUEST_LOGGING_INPUT_SLICE") != "0":
        errors.append("AGENT_REQUEST_LOGGING_INPUT_SLICE must be 0 to redact request text")

    write_enabled, write_error = read_env_bool("COLLEAGUE_SLACK_WRITE_ENABLED", False)
    _, availability_error = read_env_bool("COLLEAGUE_SLACK_AVAILABILITY_ENABLED", False)
    _, mention_error = read_env_bool("COLLEAGUE_SLACK_REQUIRE_MENTION", True)
    errors.extend(error for error in (write_error, availability_error, mention_error) if error)

    agentic_enabled, agentic_error = read_env_bool("COLLEAGUE_AGENTIC_DEVELOPMENT_ENABLED", False)
    github_write_enabled, github_write_error = read_env_bool("GITHUB_DELIVERY_WRITE_ENABLED", False)
    errors.extend(error for error in (agentic_error, github_write_error) if error)
    coder_timeout, coder_timeout_error = read_positive_int("CODING_AGENT_TIMEOUT_SECONDS", 480)
    _, approval_ttl_error = read_bounded_int("AGENTIC_DELIVERY_APPROVAL_TTL_SECONDS", 259200, 604800)
    _, agentic_stale_error = read_bounded_int("AGENTIC_DELIVERY_STALE_AFTER_DAYS", 14, 3650)
    errors.extend(error for error in (coder_timeout_error, approval_ttl_error, agentic_stale_error) if error)

    cron = os.getenv("COLLEAGUE_CRON_SCHEDULE", "*/15 * * * *")
    max_run, max_run_error = read_positive_int("COLLEAGUE_MAX_RUN_SECONDS", 600)
    _, report_error = read_positive_int("COLLEAGUE_REPORT_INTERVAL_HOURS", 36)
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
    if agentic_enabled:
        if not github_write_enabled:
            errors.append("GITHUB_DELIVERY_WRITE_ENABLED must be true when agentic development is enabled")
        required_agentic = (
            "CODING_AGENT_ALLOWED_WORKSPACES",
            "CODING_AGENT_PRIMARY_WORKSPACE",
            "CODING_AGENT_GIT_NAME",
            "CODING_AGENT_GIT_EMAIL",
            "GITHUB_PM_TOKEN",
            "GITHUB_CODER_TOKEN",
            "GITHUB_DELIVERY_ALLOWED_REPOSITORIES",
            "GITHUB_DELIVERY_PM_LOGIN",
            "GITHUB_DELIVERY_CODER_LOGIN",
            "GITHUB_DELIVERY_HUMAN_REVIEWERS",
            "GITHUB_PROJECT_ID",
            "GITHUB_PROJECT_STATUS_FIELD_ID",
            "GITHUB_PROJECT_STATUS_OPTIONS_JSON",
        )
        errors.extend(f"missing {name}" for name in required_agentic if not os.getenv(name, "").strip())
        logins = [
            os.getenv("GITHUB_DELIVERY_PM_LOGIN", "").strip(),
            os.getenv("GITHUB_DELIVERY_CODER_LOGIN", "").strip(),
            *comma_values("GITHUB_DELIVERY_HUMAN_REVIEWERS"),
        ]
        if any(login and not GITHUB_LOGIN_RE.fullmatch(login) for login in logins):
            errors.append("configured PM, coder, and reviewer GitHub logins must be valid login names")
        pm_login, coder_login, *reviewers = logins
        if pm_login.casefold() == coder_login.casefold():
            warnings.append(
                "GITHUB_DELIVERY_PM_LOGIN and GITHUB_DELIVERY_CODER_LOGIN are the same; "
                "separate automation identities are recommended"
            )
        automation = {pm_login.casefold(), coder_login.casefold()}
        if automation & {reviewer.casefold() for reviewer in reviewers}:
            warnings.append(
                "human reviewers include the configured PM or coder; "
                "a separate human reviewer is recommended"
            )
        eligible = {value.casefold() for value in comma_values("AGENTIC_DELIVERY_ELIGIBLE_STATUSES")}
        if not eligible or not eligible <= {"backlog", "to do"}:
            warnings.append(
                "AGENTIC_DELIVERY_ELIGIBLE_STATUSES includes values other than Backlog and To Do; "
                "verify the configured Project status names"
            )
        pm_token = os.getenv("GITHUB_PM_TOKEN", "").strip()
        coder_token = os.getenv("GITHUB_CODER_TOKEN", "").strip()
        if pm_token and coder_token and pm_token == coder_token:
            warnings.append(
                "GITHUB_PM_TOKEN and GITHUB_CODER_TOKEN are the same credential; "
                "separate least-privilege credentials are recommended"
            )
        launcher = (ROOT / "scripts" / "coder_codex_launcher.py").resolve()
        executable = os.getenv("CODING_AGENT_CODEX_EXECUTABLE", str(launcher)).strip()
        resolved_executable = shutil.which(executable)
        if not resolved_executable or Path(resolved_executable).resolve() != launcher:
            errors.append("CODING_AGENT_CODEX_EXECUTABLE must be the bundled fork-only launcher")
        real_executable = os.getenv("CODING_AGENT_REAL_CODEX_EXECUTABLE", "codex").strip()
        if not real_executable or shutil.which(real_executable) is None:
            errors.append("CODING_AGENT_REAL_CODEX_EXECUTABLE was not found")
        git_email = os.getenv("CODING_AGENT_GIT_EMAIL", "").strip()
        if git_email and (
            len(git_email) > 254
            or git_email.count("@") != 1
            or any(character.isspace() for character in git_email)
        ):
            errors.append("CODING_AGENT_GIT_EMAIL must be a valid non-whitespace email address")
        if coder_timeout > max_run - 60:
            errors.append(
                "CODING_AGENT_TIMEOUT_SECONDS must leave at least 60 seconds "
                "inside COLLEAGUE_MAX_RUN_SECONDS"
            )
        primary = Path(os.getenv("CODING_AGENT_PRIMARY_WORKSPACE", "")).expanduser().resolve()
        roots = [
            Path(value).expanduser().resolve()
            for value in os.getenv("CODING_AGENT_ALLOWED_WORKSPACES", "").split(os.pathsep)
            if value.strip()
        ]
        if (
            not primary.is_dir()
            or not (primary / ".git").exists()
            or not any(primary == root or primary.is_relative_to(root) for root in roots)
        ):
            errors.append("CODING_AGENT_PRIMARY_WORKSPACE must be an existing allowed Git workspace")
        node_id_re = re.compile(r"[A-Za-z0-9_=-]{1,200}")
        for name in ("GITHUB_PROJECT_ID", "GITHUB_PROJECT_STATUS_FIELD_ID"):
            if not node_id_re.fullmatch(os.getenv(name, "").strip()):
                errors.append(f"{name} must be a GitHub node ID")
        try:
            status_options = json.loads(os.getenv("GITHUB_PROJECT_STATUS_OPTIONS_JSON", "{}"))
        except json.JSONDecodeError:
            status_options = {}
            errors.append("GITHUB_PROJECT_STATUS_OPTIONS_JSON must be valid JSON")
        if not isinstance(status_options, dict) or not {"In Progress", "In Review"} <= set(status_options):
            errors.append("GITHUB_PROJECT_STATUS_OPTIONS_JSON must map In Progress and In Review")
        elif any(not node_id_re.fullmatch(str(value)) for value in status_options.values()):
            errors.append("GitHub Project status option values must be node IDs")
    elif github_write_enabled:
        warnings.append("GitHub delivery writes are enabled while agentic development is disabled")
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
    if not agentic_enabled:
        warnings.append("Agentic development is disabled until the verification canary is complete")
    daily_summary_recipients, daily_summary_error = validate_daily_summary_recipients(
        os.getenv("COLLEAGUE_DAILY_SUMMARY_TO", ""),
        os.getenv("GMAIL_ALLOWED_RECIPIENTS", ""),
    )
    if daily_summary_recipients and daily_summary_error:
        warnings.append(f"{daily_summary_error}; summaries will not send")

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
