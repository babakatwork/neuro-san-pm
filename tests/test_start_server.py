import sys

import pytest
from scripts.start_server import server_command


def test_server_command_uses_current_environment(monkeypatch):
    monkeypatch.setenv("NEURO_SAN_SERVER_HTTP_PORT", "8080")

    command = server_command()

    assert command == [
        sys.executable,
        "-m",
        "neuro_san_studio",
        "run",
        "--server-only",
        "--server-http-port",
        "8080",
    ]


@pytest.mark.parametrize("value", ["zero", "0", "8188"])
def test_server_command_rejects_invalid_port(monkeypatch, value):
    monkeypatch.setenv("NEURO_SAN_SERVER_HTTP_PORT", value)

    with pytest.raises(ValueError):
        server_command()
