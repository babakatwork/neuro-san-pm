#!/usr/bin/env python3
"""Launch Codex with only the least-privileged coder GitHub credential."""

from __future__ import annotations

import os
import secrets
import shutil
import sys
from pathlib import Path


def main() -> int:
    coder_token = os.getenv("GITHUB_CODER_TOKEN", "").strip()
    pm_token = os.getenv("GITHUB_PM_TOKEN", "").strip()
    if not coder_token or not pm_token:
        print("Non-empty PM and coder GitHub tokens are required.", file=sys.stderr)
        return 78
    if secrets.compare_digest(coder_token, pm_token):
        print(
            "WARNING: PM and coder GitHub tokens are identical; credential separation is disabled.",
            file=sys.stderr,
        )

    configured = os.getenv("CODING_AGENT_REAL_CODEX_EXECUTABLE", "codex").strip()
    executable = shutil.which(configured)
    if not executable or Path(executable).resolve() == Path(__file__).resolve():
        print("The real Codex executable is unavailable.", file=sys.stderr)
        return 78

    env = os.environ.copy()
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PM_TOKEN", "GITHUB_CODER_TOKEN"):
        env.pop(name, None)
    env.pop("SSH_AUTH_SOCK", None)
    env.update(
        {
            "GITHUB_TOKEN": coder_token,
            "GH_TOKEN": coder_token,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_ASKPASS": str(Path(__file__).with_name("github_coder_askpass.py")),
            "GIT_ALLOW_PROTOCOL": "https:file",
            "GIT_AUTHOR_NAME": os.getenv("CODING_AGENT_GIT_NAME", "neuro-san coder"),
            "GIT_AUTHOR_EMAIL": os.getenv("CODING_AGENT_GIT_EMAIL", ""),
            "GIT_COMMITTER_NAME": os.getenv("CODING_AGENT_GIT_NAME", "neuro-san coder"),
            "GIT_COMMITTER_EMAIL": os.getenv("CODING_AGENT_GIT_EMAIL", ""),
            "NEURO_SAN_CODER_FORK_ONLY": "true",
        }
    )
    os.execve(executable, [executable, *sys.argv[1:]], env)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
