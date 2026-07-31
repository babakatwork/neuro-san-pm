"""Contract tests for the externally maintained agentic coder tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from coded_tools.coding_agent.codex_backend import BackendResponse
from coded_tools.coding_agent.codex_backend import CodingRequest
from coded_tools.coding_agent.coding_agent import CodingAgent


class RecordingBackend:
    """Small backend double proving PM delegates through CodingAgent.start."""

    def __init__(self) -> None:
        self.requests: list[CodingRequest] = []

    async def start(self, handle: str, request: CodingRequest) -> BackendResponse:
        del handle
        self.requests.append(request)
        return BackendResponse(
            {
                "status": "completed",
                "summary": "Opened a pull request.",
                "details": "Implementation and tests are ready for PM review.",
                "branch": "codex/issue-123",
                "commit": "abc1234",
                "pull_request_url": "https://github.com/example/project/pull/456",
                "question": "",
            },
            "codex-session-id",
        )

    async def resume(self, handle: str, provider_session_id: str, response: str) -> BackendResponse:
        del handle, provider_session_id, response
        raise AssertionError("PM assignments must start fresh rather than resume a prior Codex session")

    async def cancel(self, handle: str) -> bool:
        del handle
        return False


def _start(tool: CodingAgent, workspace: Path, context: str, sly_data: dict[str, Any]) -> dict[str, Any]:
    raw = asyncio.run(
        tool.async_invoke(
            {
                "operation": "start",
                "task": "Implement issue #123 and open a pull request; do not merge it.",
                "workspace": str(workspace),
                "repository": "https://github.com/example/project",
                "context": context,
            },
            sly_data,
        )
    )
    return json.loads(raw)


def test_dependency_tool_is_importable_alongside_pm_tools() -> None:
    from coded_tools.colleague.colleague_state import ColleagueState

    assert CodingAgent.__module__ == "coded_tools.coding_agent.coding_agent"
    assert ColleagueState.__module__ == "coded_tools.colleague.colleague_state"
    assert "vendor/neuro-san-coder/coded_tools" in str(
        Path(__import__("coded_tools.coding_agent").coding_agent.__file__).as_posix()
    )


def test_each_ticket_assignment_starts_with_fresh_conversation_state(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CODING_AGENT_ALLOWED_WORKSPACES", str(tmp_path))
    backend = RecordingBackend()
    tool = CodingAgent(backend=backend)

    first_state: dict[str, Any] = {}
    first = _start(tool, tmp_path, "Issue body and its first review cycle.", first_state)
    second_state: dict[str, Any] = {}
    second = _start(tool, tmp_path, "Issue, PR, and follow-up comments reloaded from GitHub.", second_state)

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert first["session_id"] != second["session_id"]
    assert set(first_state) == {"_coding_agent_sessions"}
    assert set(second_state) == {"_coding_agent_sessions"}
    assert len(first_state["_coding_agent_sessions"]) == 1
    assert len(second_state["_coding_agent_sessions"]) == 1
    assert [request.context for request in backend.requests] == [
        "Issue body and its first review cycle.",
        "Issue, PR, and follow-up comments reloaded from GitHub.",
    ]
    assert all(request.repository == "https://github.com/example/project" for request in backend.requests)
