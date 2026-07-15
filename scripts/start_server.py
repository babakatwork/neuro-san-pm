"""Validate the permanent deployment contract, then replace this process with Neuro SAN."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from scripts.check_config import main as check_config
from scripts.slack_availability import set_availability

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVER_PORT = 8188
NEURO_SAN_DEFAULT_PORT = 8080


def configure_core_environment() -> None:
    """Point Neuro SAN core at this standalone project's runtime assets."""
    paths = {
        "AGENT_MANIFEST_FILE": ROOT / "registries" / "manifest.hocon",
        "AGENT_TOOL_PATH": ROOT / "coded_tools",
        "MCP_SERVERS_INFO_FILE": ROOT / "mcp" / "mcp_info.hocon",
    }
    for name, path in paths.items():
        os.environ[name] = str(path)

    current_pythonpath = os.getenv("PYTHONPATH", "")
    entries = [entry for entry in current_pythonpath.split(os.pathsep) if entry]
    root = str(ROOT)
    os.environ["PYTHONPATH"] = os.pathsep.join([root, *(entry for entry in entries if entry != root)])
    os.environ.setdefault("AGENT_MANIFEST_UPDATE_PERIOD_SECONDS", "5")


def server_command() -> list[str]:
    """Return the exact single-worker server command after validating its port."""
    raw_port = os.getenv("NEURO_SAN_SERVER_HTTP_PORT", str(DEFAULT_SERVER_PORT))
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("NEURO_SAN_SERVER_HTTP_PORT must be an integer") from exc
    if not 1024 <= port <= 65535:
        raise ValueError("NEURO_SAN_SERVER_HTTP_PORT must be between 1024 and 65535")
    if port == NEURO_SAN_DEFAULT_PORT:
        raise ValueError("NEURO_SAN_SERVER_HTTP_PORT must not use Neuro SAN's default port 8080")
    return [
        sys.executable,
        "-m",
        "neuro_san.service.main_loop.server_main_loop",
        "--http_port",
        str(port),
        "--http_server_instances",
        "1",
        "--manifest_update_period_seconds",
        os.getenv("AGENT_MANIFEST_UPDATE_PERIOD_SECONDS", "5"),
    ]


def main() -> int:
    """Fail closed on configuration errors, otherwise exec the server."""
    if check_config() != 0:
        return 1
    configure_core_environment()
    try:
        command = server_command()
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    set_availability("online")
    os.execv(command[0], command)
    return 0  # pragma: no cover - os.execv replaces the process on success


if __name__ == "__main__":
    sys.exit(main())
