import asyncio
import json
import socket
import sys
import time
from pathlib import Path

import pytest
import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dulus_bar.server import AgentEventServer


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def hub():
    port = free_port()
    server = AgentEventServer(port=port)
    server.start()
    yield server, f"ws://127.0.0.1:{port}"
    server.stop()
    time.sleep(0.05)


def test_event_relay_and_local_emit(hub):
    server, url = hub
    emitted = []
    server.on_event(emitted.append)

    async def scenario():
        async with websockets.connect(url) as sender, websockets.connect(url) as ui:
            event = {
                "agent": "Dulus",
                "type": "message",
                "session_id": "abc",
                "payload": {"text": "Working", "ctx": "42%"},
            }
            await sender.send(json.dumps(event))
            assert json.loads(await asyncio.wait_for(ui.recv(), 1)) == event
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(sender.recv(), 0.1)

    asyncio.run(scenario())
    assert len(emitted) == 1
    assert emitted[0].agent == "Dulus"
    assert emitted[0].payload["ctx"] == "42%"


def test_decision_relays_without_local_emit(hub):
    server, url = hub
    emitted = []
    server.on_event(emitted.append)

    async def scenario():
        async with websockets.connect(url) as wrapper, websockets.connect(url) as ui:
            decision = {
                "agent": "Dulus",
                "type": "decision",
                "session_id": "abc",
                "payload": {"approved": True},
            }
            await ui.send(json.dumps(decision))
            assert json.loads(await asyncio.wait_for(wrapper.recv(), 1)) == decision
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ui.recv(), 0.1)

    asyncio.run(scenario())
    assert emitted == []


def test_health_is_not_relayed(hub):
    _, url = hub

    async def scenario():
        async with websockets.connect(url) as probe, websockets.connect(url) as ui:
            await probe.send(json.dumps({
                "agent": "_health", "type": "ping",
                "session_id": "_health", "payload": {},
            }))
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ui.recv(), 0.1)

    asyncio.run(scenario())
