"""Validate the permanent deployment contract, then replace this process with Neuro SAN."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from scripts.check_config import main as check_config


def server_command() -> list[str]:
    """Return the exact single-worker server command after validating its port."""
    raw_port = os.getenv("NEURO_SAN_SERVER_HTTP_PORT", "8080")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("NEURO_SAN_SERVER_HTTP_PORT must be an integer") from exc
    if port != 8080:
        raise ValueError("NEURO_SAN_SERVER_HTTP_PORT must be 8080 for this deployment contract")
    executable = Path(sys.executable).with_name("ns")
    return [str(executable), "run", "--server-only", "--server-http-port", str(port)]


def main() -> int:
    """Fail closed on configuration errors, otherwise exec the server."""
    if check_config() != 0:
        return 1
    try:
        command = server_command()
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    os.execv(command[0], command)
    return 0  # pragma: no cover - os.execv replaces the process on success


if __name__ == "__main__":
    sys.exit(main())
