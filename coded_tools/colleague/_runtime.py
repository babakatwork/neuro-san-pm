"""Small runtime helpers shared by colleague coded tools."""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path
from threading import RLock
from typing import Any
from typing import Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


_PROCESS_LOCK = RLock()
_TRUE_ENV_VALUES = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSE_ENV_VALUES = frozenset({"0", "false", "f", "no", "n", "off"})


def read_env_bool(name: str, default: bool = False) -> tuple[bool, str | None]:
    """Read a boolean environment variable and report invalid values.

    Unknown values resolve to the caller-provided safe default. Callers that
    validate configuration can surface the returned error, while enforcement
    callers can use :func:`env_bool` directly. Capability gates should default
    to ``False``; restrictive policy gates can default to ``True``.
    """
    value = os.getenv(name)
    if value is None:
        return default, None
    normalized = value.strip().lower()
    if normalized in _TRUE_ENV_VALUES:
        return True, None
    if normalized in _FALSE_ENV_VALUES:
        return False, None
    return default, f"{name} must be a recognized boolean (true/false)"


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable, failing closed on invalid input."""
    value, _ = read_env_bool(name, default)
    return value


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return utc_now().isoformat().replace("+00:00", "Z")


def json_result(**values: Any) -> str:
    """Serialize a coded-tool result consistently."""
    return json.dumps(values, sort_keys=True, ensure_ascii=False)


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    """Read a JSON object, returning a copy of default when absent."""
    if not path.exists():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"State file is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"State file must contain a JSON object: {path}")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically replace a JSON state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Use a process and, where available, OS-level exclusive lock."""
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _PROCESS_LOCK:
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def has_active_lease(run_id: str) -> bool:
    """Return whether run_id owns the current unexpired colleague lease."""
    if not run_id:
        return False
    state_path = Path(os.getenv("COLLEAGUE_STATE_PATH", ".state/colleague.json"))
    try:
        with exclusive_file_lock(state_path):
            state = read_json(state_path, {})
        active = state.get("run")
        return bool(
            isinstance(active, dict)
            and active.get("id") == run_id
            and float(active.get("lease_until", 0)) > time.time()
        )
    except (OSError, TypeError, ValueError):
        return False


def append_audit(event: str, **fields: Any) -> None:
    """Append a secret-free audit event. Failures never break the main action."""
    path = Path(os.getenv("COLLEAGUE_AUDIT_PATH", ".state/audit.jsonl"))
    record = {"at": utc_now_iso(), "event": event, **fields}
    try:
        with exclusive_file_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False))
                handle.write("\n")
    except OSError:
        return
