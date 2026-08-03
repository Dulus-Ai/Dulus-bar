"""WebSocket event server that receives agent status from wrappers."""

import asyncio
import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import websockets


@dataclass
class AgentEvent:
    """Normalized event from an agent wrapper."""

    agent: str
    event_type: str  # session_started, message, tool_request, tool_approved, tool_denied, completed, error
    session_id: str
    payload: Dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


EventHandler = Callable[[AgentEvent], None]


class AgentEventServer:
    """Tiny WebSocket hub that agent wrappers connect to."""

    def __init__(self, host: str = "127.0.0.1", port: int = 17372):
        self.host = host
        self.port = port
        self._handlers: List[EventHandler] = []
        self._clients: set = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._server: Any = None

    def on_event(self, handler: EventHandler) -> EventHandler:
        self._handlers.append(handler)
        return handler

    def remove_handler(self, handler: EventHandler) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)

    def _emit(self, event: AgentEvent) -> None:
        for handler in list(self._handlers):
            try:
                handler(event)
            except Exception as exc:
                print(f"[event_server] handler error: {exc}")

    async def _relay(self, data: Dict, *, exclude: Any = None) -> None:
        """Relay a protocol message to every peer except its sender.

        The original Qt overlay lived in this process and consumed events via
        callbacks.  The native macOS surface is a separate WebSocket client, so
        the server also acts as a tiny hub: wrappers -> UI and UI -> wrappers.
        """
        peers = [client for client in list(self._clients) if client is not exclude]
        if not peers:
            return
        encoded = json.dumps(data)
        await asyncio.gather(
            *(client.send(encoded) for client in peers),
            return_exceptions=True,
        )

    async def _handle(self, websocket: Any) -> None:
        self._clients.add(websocket)
        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue

                event_type = data.get("type", "message")
                agent = data.get("agent", "unknown")
                # Silent no-op for health probes (don't spam handlers / UI)
                if event_type in ("ping", "health", "pong") or agent in ("_health", "VibeHealth", "DulusHealth"):
                    continue

                # Decisions originate in a UI client. They belong to wrappers,
                # not to local event reducers, so relay them without emitting.
                if event_type == "decision":
                    await self._relay(data, exclude=websocket)
                    continue

                event = AgentEvent(
                    agent=agent,
                    event_type=event_type,
                    session_id=data.get("session_id", "default"),
                    payload=data.get("payload", {}) or {},
                )
                self._emit(event)
                await self._relay(data, exclude=websocket)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            # Handshake noise is non-fatal, but retain useful diagnostics for
            # genuine protocol/client failures.
            print(f"[event_server] client error: {exc}")
        finally:
            self._clients.discard(websocket)

    async def _run(self) -> None:
        self._stop_event = asyncio.Event()
        server = await websockets.serve(self._handle, self.host, self.port, ping_interval=None)
        self._server = server
        await self._stop_event.wait()
        server.close()
        await server.wait_closed()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        ready = threading.Event()
        error_box: list = []

        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._run_and_signal(ready, error_box))
            except Exception as exc:
                error_box.append(exc)
                ready.set()
                print(f"[event_server] loop ended: {exc}")

        self._thread = threading.Thread(target=run_loop, daemon=True, name="dulus-bar-ws")
        self._thread.start()
        if not ready.wait(timeout=5):
            print(f"[event_server] WARN: server not ready after 5s on {self.host}:{self.port}")
        elif error_box:
            print(f"[event_server] FAILED: {error_box[0]}")
        else:
            print(f"[event_server] listening on ws://{self.host}:{self.port}")

    async def _run_and_signal(self, ready: threading.Event, error_box: list) -> None:
        self._stop_event = asyncio.Event()
        try:
            server = await websockets.serve(
                self._handle,
                self.host,
                self.port,
                ping_interval=20,
                ping_timeout=20,
                max_size=2**20,
            )
        except OSError as exc:
            error_box.append(exc)
            ready.set()
            raise
        self._server = server
        ready.set()
        await self._stop_event.wait()
        server.close()
        await server.wait_closed()

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def broadcast(self, data: Dict) -> None:
        """Send a message to all connected wrapper clients."""
        if not self._loop:
            return

        async def _send() -> None:
            if self._clients:
                await asyncio.gather(
                    *[client.send(json.dumps(data)) for client in self._clients],
                    return_exceptions=True,
                )

        asyncio.run_coroutine_threadsafe(_send(), self._loop)


if __name__ == "__main__":
    server = AgentEventServer()
    server.on_event(lambda e: print(e))
    server.start()
    try:
        while True:
            pass
    except KeyboardInterrupt:
        server.stop()
