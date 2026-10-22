"""Local WebSocket hub: agents report in, island UIs listen and answer.

Roles (see PROTOCOL.md):

* **agent** — wrappers, Claude Code / Codex hooks, ``dulusbar notify``, any
  SDK client. Sends events; receives ``decision`` / ``request_resolved`` for
  its *own* tool requests only. No auth needed, so v1 clients keep working.
* **ui** — the native macOS surface (the Qt island lives in-process and uses
  callbacks). Receives every agent event plus a snapshot on connect, and is
  the only peer that may answer a request. Requires the per-run hub token.

Browsers can reach ``ws://127.0.0.1`` from any web page, so connections that
carry an ``Origin`` header are refused outright: no website gets to read your
agents' tool calls, let alone approve them.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import websockets

from . import __version__
from . import protocol as P
from .protocol import AgentEvent  # noqa: F401  (re-exported: older code imports it from here)
from .state import Permission, Session, SessionStore

EventHandler = Callable[[AgentEvent], None]

HOUSEKEEPING_SECONDS = 5.0
STALE_AFTER_SECONDS = 30 * 60
# Hub-generated event types a client is not allowed to forge.
_HUB_ONLY = frozenset({
    P.DISCONNECTED, P.SESSION_REMOVED, P.REQUEST_RESOLVED, P.SNAPSHOT, P.WELCOME,
})


def pid_alive(pid: int) -> Optional[bool]:
    """True / False when we can tell, None when we can't."""
    if not pid or pid <= 0:
        return None
    try:
        import psutil

        if not psutil.pid_exists(pid):
            return False
        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return True
    except ImportError:
        pass
    if os.name == "nt":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _env_origins() -> List[str]:
    raw = os.environ.get("DULUS_BAR_ALLOWED_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


@dataclass(eq=False)
class _Peer:
    ws: Any
    role: str = P.ROLE_AGENT
    authed: bool = False
    ephemeral: bool = False  # one-shot clients (hooks, CLI): a disconnect ends nothing
    client: str = ""
    owned: Set[Tuple[str, str]] = field(default_factory=set)
    requests: Set[str] = field(default_factory=set)


class AgentEventServer:
    """Tiny WebSocket hub that agents and island UIs connect to."""

    def __init__(
        self,
        host: str = P.DEFAULT_HOST,
        port: int = P.DEFAULT_PORT,
        *,
        token: Optional[str] = None,
        publish: bool = False,
        allowed_origins: Optional[List[str]] = None,
    ):
        self.host = host
        self.port = port
        self.token = token or P.new_token()
        self.publish = publish  # write hub.json (the app does; tests don't)
        extra = _env_origins() if allowed_origins is None else list(allowed_origins)
        # No Origin (every native client), or the hub's own origin: no web page
        # can have that one, because the hub never serves a page.
        own = [f"{scheme}://{name}:{port}" for scheme in ("http", "ws")
               for name in ("127.0.0.1", "localhost")]
        self._origins: List[Optional[str]] = [None, *own, *extra]
        self.store = SessionStore()
        self.started_at = 0.0
        self.error: Optional[BaseException] = None
        self._host_id = P.host_id()
        self._handlers: List[EventHandler] = []
        self._shutdown_handlers: List[Callable[[], None]] = []
        self._peers: Dict[Any, _Peer] = {}
        self._owners: Dict[str, _Peer] = {}  # request_id -> the peer waiting on it
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._server: Any = None

    # --- in-process subscribers ----------------------------------------------
    def on_event(self, handler: EventHandler) -> EventHandler:
        self._handlers.append(handler)
        return handler

    def remove_handler(self, handler: EventHandler) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)

    def on_shutdown(self, handler: Callable[[], None]) -> None:
        """Called (on the hub thread) when a client runs ``dulusbar quit``."""
        self._shutdown_handlers.append(handler)

    def _emit(self, event: AgentEvent) -> None:
        for handler in list(self._handlers):
            try:
                handler(event)
            except Exception as exc:
                print(f"[event_server] handler error: {exc}")

    # --- sending -------------------------------------------------------------
    async def _send(self, peer: _Peer, data: Dict[str, Any]) -> None:
        try:
            await peer.ws.send(json.dumps(data))
        except Exception:
            pass

    async def _send_many(self, peers: List[_Peer], data: Dict[str, Any]) -> None:
        if not peers:
            return
        encoded = json.dumps(data)

        async def one(peer: _Peer) -> None:
            try:
                await peer.ws.send(encoded)
            except Exception:
                pass

        await asyncio.gather(*(one(peer) for peer in peers))

    async def _send_ui(self, data: Dict[str, Any]) -> None:
        await self._send_many([p for p in self._peers.values() if p.role == P.ROLE_UI], data)

    # --- connection handling ---------------------------------------------------
    async def _handle(self, websocket: Any) -> None:
        peer = _Peer(ws=websocket)
        self._peers[websocket] = peer
        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if isinstance(data, dict):
                    await self._dispatch(peer, data)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            print(f"[event_server] client error: {exc}")
        finally:
            self._peers.pop(websocket, None)
            await self._drop_peer(peer)

    async def _dispatch(self, peer: _Peer, data: Dict[str, Any]) -> None:
        kind = str(data.get("type") or P.MESSAGE)
        if kind == P.HELLO:
            await self._on_hello(peer, data)
            return
        if kind == P.STATUS:
            await self._on_status(peer, data)
            return
        if kind == P.SHUTDOWN:
            await self._on_shutdown(peer, data)
            return
        if kind == P.DECISION:
            # Only an authenticated island may answer a tool request.
            if peer.role == P.ROLE_UI and peer.authed:
                await self._decide(str(data.get("request_id") or ""), data, by="ui")
            return
        if kind == P.CANCEL:
            await self._on_cancel(peer, str(data.get("request_id") or ""))
            return
        agent = str(data.get("agent") or "unknown")
        session_id = str(data.get("session_id") or "default")
        if kind in _HUB_ONLY or peer.role == P.ROLE_UI or P.is_health(agent, kind, session_id):
            return

        event = AgentEvent.from_message(data)
        if not event.ts:
            event.ts = time.time()
        if kind == P.TOOL_REQUEST and not self._claim_request(peer, event):
            return  # an old wrapper re-reporting the prompt that's already up
        if not peer.ephemeral:
            peer.owned.add((event.agent, event.session_id))
        resolved = self.store.apply(event)
        self._emit(event)
        relayed = dict(data)
        relayed.update(event.to_message())
        await self._send_ui(relayed)
        for perm in resolved:
            await self._announce(perm, "answered", by="agent")

    def _claim_request(self, peer: _Peer, event: AgentEvent) -> bool:
        """Give the request an id and remember who is waiting on it."""
        if not event.request_id:
            probe = Permission.from_event(event)
            for rid in peer.requests:
                pending = self.store.find_permission(rid)
                if (
                    pending is not None
                    and pending.key == probe.key
                    and (pending.tool, pending.args) == (probe.tool, probe.args)
                ):
                    return False
            event.request_id = P.new_request_id()
        elif event.request_id in self._owners:
            return False  # duplicate delivery of a request we already track
        peer.requests.add(event.request_id)
        self._owners[event.request_id] = peer
        return True

    def _token_ok(self, token: Any) -> bool:
        return isinstance(token, str) and bool(token) and hmac.compare_digest(token, self.token)

    async def _on_hello(self, peer: _Peer, data: Dict[str, Any]) -> None:
        role = data.get("role") if data.get("role") in (P.ROLE_AGENT, P.ROLE_UI) else P.ROLE_AGENT
        authed = self._token_ok(data.get("token"))
        if role == P.ROLE_UI and not authed:
            await self._send(peer, {"type": "error", "error": "unauthorized",
                                    "message": "the ui role needs the hub token"})
            await peer.ws.close(code=1008, reason="unauthorized")
            return
        peer.role = role
        peer.authed = authed
        peer.ephemeral = bool(data.get("ephemeral"))
        peer.client = str(data.get("client") or "")[:80]
        await self._send(peer, {
            "type": P.WELCOME,
            "v": P.PROTOCOL_VERSION,
            "server": f"dulus-bar/{__version__}",
            "role": peer.role,
            "authed": authed,
            "host_id": self._host_id,
        })
        if peer.role == P.ROLE_UI:
            await self._send(peer, {"type": P.SNAPSHOT, "v": P.PROTOCOL_VERSION, **self.store.snapshot()})

    async def _on_status(self, peer: _Peer, data: Dict[str, Any]) -> None:
        if not (peer.authed or self._token_ok(data.get("token"))):
            await self._send(peer, {"type": "error", "error": "unauthorized"})
            return
        roles = [p.role for p in self._peers.values()]
        await self._send(peer, {
            "type": P.STATUS,
            "v": P.PROTOCOL_VERSION,
            "server": {
                "version": __version__,
                "pid": os.getpid(),
                "port": self.port,
                "started_at": self.started_at,
                "agents_connected": roles.count(P.ROLE_AGENT),
                "uis_connected": roles.count(P.ROLE_UI),
            },
            **self.store.snapshot(),
        })

    async def _on_shutdown(self, peer: _Peer, data: Dict[str, Any]) -> None:
        if not (peer.authed or self._token_ok(data.get("token"))):
            await self._send(peer, {"type": "error", "error": "unauthorized"})
            return
        await self._send(peer, {"type": P.SHUTDOWN, "ok": True})
        handlers = list(self._shutdown_handlers)
        if not handlers and self._stop_event is not None:
            self._stop_event.set()
        for handler in handlers:
            try:
                handler()
            except Exception as exc:
                print(f"[event_server] shutdown handler error: {exc}")

    async def _on_cancel(self, peer: _Peer, request_id: str) -> None:
        if request_id not in peer.requests:
            return
        perm = self.store.resolve(request_id)
        if perm is not None:
            await self._announce(perm, "cancelled", by="agent")
        else:
            self._release(request_id)

    async def _decide(self, request_id: str, data: Dict[str, Any], *, by: str) -> bool:
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        approved = bool(payload.get("approved"))
        outcome = "approved" if approved else "denied"
        perm = self.store.resolve(request_id, outcome) if request_id else None
        if perm is None and not request_id:
            # v1 UI: no request id, so answer the oldest prompt of that session.
            key = (str(data.get("agent") or ""), str(data.get("session_id") or ""))
            pending = self.store.pending_for(key)
            if pending:
                perm = self.store.resolve(pending[0].request_id, outcome)
        if perm is None:
            return False
        decision = {
            "type": P.DECISION,
            "agent": perm.agent,
            "session_id": perm.session_id,
            "request_id": perm.request_id,
            "payload": {
                "approved": approved,
                "always": bool(payload.get("always")) and approved,
                "message": str(payload.get("message") or ""),
            },
        }
        owner = self._owners.get(perm.request_id)
        if owner is not None:
            await self._send(owner, decision)
        else:
            # Owner unknown (it reconnected): v1 wrappers filter by session_id.
            await self._send_many([p for p in self._peers.values() if p.role == P.ROLE_AGENT], decision)
        await self._announce(perm, outcome, by=by)
        return True

    def _release(self, request_id: str) -> Optional[_Peer]:
        owner = self._owners.pop(request_id, None)
        if owner is not None:
            owner.requests.discard(request_id)
        return owner

    async def _announce(self, perm: Permission, outcome: str, *, by: str) -> None:
        """Tell everyone a request is settled, so no card is left behind."""
        owner = self._release(perm.request_id)
        msg = {
            "type": P.REQUEST_RESOLVED,
            "agent": perm.agent,
            "session_id": perm.session_id,
            "request_id": perm.request_id,
            "payload": {"outcome": outcome, "by": by},
            "ts": time.time(),
        }
        self._emit(AgentEvent.from_message(msg))
        await self._send_ui(msg)
        if owner is not None and outcome not in ("approved", "denied") and owner.ws in self._peers:
            await self._send(owner, msg)  # e.g. answered in the terminal: stop waiting

    async def _drop_peer(self, peer: _Peer) -> None:
        for request_id in list(peer.requests):
            perm = self.store.resolve(request_id)
            if perm is not None:
                await self._announce(perm, "cancelled", by="agent")
            else:
                self._release(request_id)
        if peer.ephemeral or peer.role != P.ROLE_AGENT:
            return
        # A long-lived agent socket (a wrapper) dropped: its sessions are gone,
        # unless another live socket still reports them.
        still_owned = set().union(*(p.owned for p in self._peers.values()))
        for agent, session_id in peer.owned - still_owned:
            await self._remove(agent, session_id, P.DISCONNECTED)

    async def _remove(self, agent: str, session_id: str, kind: str) -> None:
        event = AgentEvent(agent=agent, event_type=kind, session_id=session_id, ts=time.time())
        for perm in self.store.apply(event):
            await self._announce(perm, "cancelled", by="hub")
        self._emit(event)
        await self._send_ui(event.to_message())

    # --- housekeeping ----------------------------------------------------------
    def _alive(self, session: Session) -> Optional[bool]:
        if any(session.key in p.owned for p in self._peers.values()):
            return True
        if session.pid and (not session.pid_host or session.pid_host == self._host_id):
            return pid_alive(session.pid)
        return None

    async def _housekeeping(self) -> None:
        while True:
            await asyncio.sleep(HOUSEKEEPING_SECONDS)
            try:
                for agent, session_id in self.store.prune(time.time(), STALE_AFTER_SECONDS, self._alive):
                    await self._remove(agent, session_id, P.SESSION_REMOVED)
            except Exception as exc:  # never let housekeeping die
                print(f"[event_server] housekeeping error: {exc}")

    # --- lifecycle ----------------------------------------------------------------
    async def _serve(self, ready: threading.Event) -> None:
        self._stop_event = asyncio.Event()
        try:
            server = await websockets.serve(
                self._handle,
                self.host,
                self.port,
                origins=self._origins,
                # No keepalive pings: on localhost the OS reports a dead peer at
                # once, and pings only killed simple clients that sat idle.
                ping_interval=None,
                max_size=2**20,
            )
        except OSError as exc:
            self.error = exc
            ready.set()
            return
        self._server = server
        self.started_at = time.time()
        if self.publish:
            try:
                P.write_hub_info(self.port, self.token, __version__)
            except OSError as exc:
                print(f"[event_server] could not write {P.hub_info_path()}: {exc}")
        ready.set()
        housekeeping = asyncio.ensure_future(self._housekeeping())
        try:
            await self._stop_event.wait()
        finally:
            housekeeping.cancel()
            server.close()
            await server.wait_closed()
            if self.publish:
                P.clear_hub_info()

    def start(self) -> bool:
        """Start the hub on a background thread. False if the port is taken."""
        if self._thread and self._thread.is_alive():
            return self.error is None
        ready = threading.Event()

        def run_loop() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._serve(ready))
            except Exception as exc:
                self.error = exc
                ready.set()
                print(f"[event_server] loop ended: {exc}")

        self._thread = threading.Thread(target=run_loop, daemon=True, name="dulus-bar-ws")
        self._thread.start()
        if not ready.wait(timeout=5):
            print(f"[event_server] WARN: server not ready after 5s on {self.host}:{self.port}")
            return False
        if self.error is not None:
            print(f"[event_server] FAILED: {self.error}")
            return False
        print(f"[event_server] listening on ws://{self.host}:{self.port}")
        return True

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    # --- thread-safe API for the in-process island ----------------------------------
    def _submit(self, coro: Any) -> Optional[Future]:
        if not self._loop or self._loop.is_closed():
            coro.close()
            return None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def decide(self, request_id: str, approved: bool, *, always: bool = False,
               message: str = "", agent: str = "", session_id: str = "") -> Optional[Future]:
        """Answer a pending request (thread-safe)."""
        data = {"agent": agent, "session_id": session_id,
                "payload": {"approved": approved, "always": always, "message": message}}
        return self._submit(self._decide(request_id, data, by="ui"))

    def remove_session(self, agent: str, session_id: str) -> Optional[Future]:
        """Dismiss a session from every island (thread-safe)."""
        return self._submit(self._remove(agent, session_id, P.SESSION_REMOVED))

    def clear_finished(self) -> Optional[Future]:
        """Dismiss every done / failed session (thread-safe)."""

        async def run() -> None:
            for agent, session_id in self.store.finished():
                await self._remove(agent, session_id, P.SESSION_REMOVED)

        return self._submit(run())

    def broadcast(self, data: Dict) -> None:
        """Send a raw message to every client (decisions are routed properly)."""
        if data.get("type") == P.DECISION:
            self._submit(self._decide(str(data.get("request_id") or ""), data, by="ui"))
            return

        async def send_all() -> None:
            await self._send_many(list(self._peers.values()), data)

        self._submit(send_all())


if __name__ == "__main__":
    hub = AgentEventServer(port=P.configured_port(), publish=True)
    hub.on_event(lambda e: print(e))
    if hub.start():
        try:
            while hub._thread and hub._thread.is_alive():
                hub._thread.join(0.5)
        except KeyboardInterrupt:
            hub.stop()
