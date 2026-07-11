import sys
from pathlib import Path

import pytest
from scripts.start_server import server_command


def test_server_command_uses_current_environment(monkeypatch):
    monkeypatch.setenv("NEURO_SAN_SERVER_HTTP_PORT", "8080")

    command = server_command()

    assert command[0].endswith("/ns")
    assert command[1:] == ["run", "--server-only", "--server-http-port", "8080"]
    assert command[0] == str(Path(sys.executable).with_name("ns"))


@pytest.mark.parametrize("value", ["zero", "0", "8188"])
def test_server_command_rejects_invalid_port(monkeypatch, value):
    monkeypatch.setenv("NEURO_SAN_SERVER_HTTP_PORT", value)

    with pytest.raises(ValueError):
        server_command()
