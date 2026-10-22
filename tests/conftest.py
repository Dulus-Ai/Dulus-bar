import socket
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/.config, ~/.claude or ~/.codex from a test."""
    monkeypatch.setenv("DULUS_BAR_HOME", str(tmp_path / "dulus-bar"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    for name in ("DULUS_BAR_SERVER", "VIBE_ISLAND_SERVER", "DULUS_BAR_PORT"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def hub():
    from dulus_bar.server import AgentEventServer

    server = AgentEventServer(port=free_port(), token="test-token")
    assert server.start()
    yield server, f"ws://127.0.0.1:{server.port}"
    server.stop()


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
