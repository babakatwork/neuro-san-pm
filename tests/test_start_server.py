import os
import sys

import pytest
from scripts.start_server import configure_core_environment
from scripts.start_server import server_command


@pytest.mark.parametrize("port", ["8188", "8288"])
def test_server_command_uses_current_environment(monkeypatch, port):
    monkeypatch.setenv("NEURO_SAN_SERVER_HTTP_PORT", port)

    command = server_command()

    assert command == [
        sys.executable,
        "-m",
        "neuro_san.service.main_loop.server_main_loop",
        "--http_port",
        port,
        "--http_server_instances",
        "1",
        "--manifest_update_period_seconds",
        "5",
    ]


def test_core_environment_points_at_standalone_project(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")

    configure_core_environment()

    assert os.environ["AGENT_MANIFEST_FILE"].endswith("/registries/manifest.hocon")
    assert os.environ["AGENT_TOOL_PATH"].endswith("/coded_tools")
    assert os.environ["MCP_SERVERS_INFO_FILE"].endswith("/mcp/mcp_info.hocon")
    assert os.environ["PYTHONPATH"].split(os.pathsep)[0].endswith("/neuro-san-pm")


@pytest.mark.parametrize("value", ["zero", "0", "8080", "65536"])
def test_server_command_rejects_invalid_port(monkeypatch, value):
    monkeypatch.setenv("NEURO_SAN_SERVER_HTTP_PORT", value)

    with pytest.raises(ValueError):
        server_command()
