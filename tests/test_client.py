"""The stdlib WebSocket client, against the real hub (and edge cases)."""

import asyncio
import json
import threading
import time

import pytest
import websockets

from conftest import free_port, wait_for
from dulus_bar.client import BarClient, BarUnavailable, Connection, probe, send_event


def test_probe_and_fire_and_forget(hub):
    server, url = hub
    welcome = probe(url)
    assert welcome["type"] == "welcome" and welcome["server"].startswith("dulus-bar/")
    assert probe(f"ws://127.0.0.1:{free_port()}") is None
    assert send_event("Script", "completed", "build", {"text": "Built ✓"}, url=url)
    assert wait_for(lambda: ("Script", "build") in server.store.sessions)
    assert server.store.sessions[("Script", "build")].message == "Built ✓"


def test_an_agent_under_a_wrapper_joins_the_wrappers_row(monkeypatch):
    monkeypatch.setenv("DULUS_BAR_AGENT", "Dulus")
    monkeypatch.setenv("DULUS_BAR_SESSION_ID", "wrap123")
    bar = BarClient()
    assert (bar.agent, bar.session_id) == ("Dulus", "wrap123")
    assert BarClient("Claude Code", "explicit").session_id == "explicit"  # explicit always wins


def test_no_hub_fails_fast():
    started = time.monotonic()
    with pytest.raises(BarUnavailable):
        BarClient(url=f"ws://127.0.0.1:{free_port()}").connect()
    assert not send_event("x", "message", "s", url=f"ws://127.0.0.1:{free_port()}")
    assert time.monotonic() - started < 1.0


def test_permission_round_trip_with_always(hub):
    server, url = hub
    result = {}

    def ask():
        with BarClient("Claude Code", "s1", url=url) as bar:
            result["d"] = bar.request_permission("Bash", "npm test", can_always=True,
                                                 always_label="Bash(npm test:*)")

    thread = threading.Thread(target=ask)
    thread.start()
    assert wait_for(lambda: server.store.permissions)
    perm = server.store.permissions[0]
    assert (perm.can_always, perm.always_label) == (True, "Bash(npm test:*)")
    server.decide(perm.request_id, True, always=True).result(2)
    thread.join(2)
    assert result["d"].approved and result["d"].always


def test_timeout_withdraws_the_card(hub):
    server, url = hub
    with BarClient("Script", "s2", url=url) as bar:
        assert bar.request_permission("Deploy?", timeout=0.2) is None
    assert wait_for(lambda: not server.store.permissions)


def test_hub_going_away_mid_wait_returns_none():
    from dulus_bar.server import AgentEventServer

    server = AgentEventServer(port=free_port())
    assert server.start()
    url = f"ws://127.0.0.1:{server.port}"
    result = {}

    def ask():
        with BarClient("Script", "s", url=url) as bar:
            result["d"] = bar.request_permission("Continue?")

    thread = threading.Thread(target=ask)
    thread.start()
    assert wait_for(lambda: server.store.permissions)
    server.stop()
    thread.join(3)
    assert result == {"d": None}


def _serve(handler):
    """A bare websockets server running `handler`, for protocol edge cases."""
    port = free_port()
    ready = threading.Event()
    stop = {}

    def run():
        async def main():
            async with websockets.serve(handler, "127.0.0.1", port, max_size=None):
                stop["loop"] = asyncio.get_running_loop()
                stop["event"] = asyncio.Event()
                ready.set()
                await stop["event"].wait()

        asyncio.run(main())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    ready.wait(3)
    return f"ws://127.0.0.1:{port}", lambda: stop["loop"].call_soon_threadsafe(stop["event"].set)


def test_large_fragmented_and_pinged_messages_arrive_intact():
    big = {"type": "decision", "payload": {"blob": "x" * 200_000}}

    async def handler(ws):
        await ws.recv()  # the client's hello
        await ws.ping()
        await ws.send(json.dumps({"type": "small"}))
        await ws.send(json.dumps(big))
        await ws.send(iter(['{"type": "frag', 'mented", "n": 3}']))  # a fragmented message
        await asyncio.sleep(0.5)

    url, stop = _serve(handler)
    try:
        bar = BarClient(url=url).connect()
        conn = bar.connection
        assert conn.recv_json(2) == {"type": "small"}
        assert conn.recv_json(2) == big
        assert conn.recv_json(2) == {"type": "fragmented", "n": 3}
        assert conn.recv_json(0.05) is None  # a timeout, not a desync
        bar.close()
    finally:
        stop()


def test_non_websocket_listener_is_not_mistaken_for_a_hub():
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def answer():
        conn, _ = listener.accept()
        conn.recv(1024)
        conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        conn.close()

    threading.Thread(target=answer, daemon=True).start()
    try:
        with pytest.raises(BarUnavailable):
            Connection(f"ws://127.0.0.1:{port}", timeout=1)
    finally:
        listener.close()
