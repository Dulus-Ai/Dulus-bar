"""Zero-dependency client for the Dulus Bar hub.

Standard library only (no ``websockets``, no asyncio), so it starts in a few
milliseconds — Claude Code runs a hook for every tool call — and so any agent,
Interant included, can vendor this one file and talk to the island::

    from dulus_bar.client import BarClient

    with BarClient(agent="My Agent") as bar:
        bar.event("session_started", {"model": "kimi-k2.5", "cwd": os.getcwd()})
        decision = bar.request_permission("Bash", "rm -rf build/")
        if decision and decision.approved:
            ...

Speaks the subset of RFC 6455 a localhost client needs: the client
handshake, masked text frames, fragmentation, ping/pong and the close
handshake (so nothing we sent is lost to a TCP reset on exit).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import select
import socket
import struct
import time
import uuid
from typing import Any, Dict, NamedTuple, Optional, Tuple
from urllib.parse import urlsplit

try:
    from .protocol import read_hub_info, server_url
except ImportError:  # vendored as a single file
    def server_url() -> str:  # type: ignore[misc]
        return os.environ.get("DULUS_BAR_SERVER") or "ws://127.0.0.1:17372"

    def read_hub_info() -> Dict[str, Any]:  # type: ignore[misc]
        return {}

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_MESSAGE = 4 * 1024 * 1024
_OP_CONT, _OP_TEXT, _OP_BINARY, _OP_CLOSE, _OP_PING, _OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class BarUnavailable(ConnectionError):
    """No Dulus Bar hub is listening (or it went away)."""


class BarError(RuntimeError):
    """The hub refused the request (e.g. a missing token)."""


class Decision(NamedTuple):
    approved: bool
    always: bool = False
    message: str = ""
    request_id: str = ""


def _mask(data: bytes, key: bytes) -> bytes:
    if not data:
        return data
    n = len(data)
    pad = int.from_bytes((key * (n // 4 + 1))[:n], "big")
    return (int.from_bytes(data, "big") ^ pad).to_bytes(n, "big")


class Connection:
    """A minimal blocking WebSocket client connection."""

    def __init__(self, url: str, timeout: float = 2.0):
        parts = urlsplit(url)
        if parts.scheme != "ws":
            raise ValueError(f"unsupported URL (ws:// only): {url}")
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 80
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise BarUnavailable(f"no Dulus Bar at {url} ({exc})") from exc
        self._buf = b""
        self._frags: list = []
        self.closed = False
        self._closing = False
        try:
            self._handshake(host, port, path, timeout)
        except Exception:
            self._sock.close()
            raise

    def _handshake(self, host: str, port: int, path: str, timeout: float) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: dulus-bar-client\r\n\r\n"
        )
        self._sock.settimeout(timeout)
        try:
            self._sock.sendall(request.encode())
            while b"\r\n\r\n" not in self._buf:
                chunk = self._sock.recv(4096)
                if not chunk or len(self._buf) > 65536:
                    raise BarUnavailable("not a WebSocket server")
                self._buf += chunk
        except OSError as exc:
            raise BarUnavailable(f"handshake failed ({exc})") from exc
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
            raise BarUnavailable(f"handshake refused: {lines[0]}")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise BarUnavailable("bad Sec-WebSocket-Accept")

    # --- frames ----------------------------------------------------------------
    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        key = os.urandom(4)
        try:
            self._sock.sendall(bytes(header) + key + _mask(payload, key))
        except OSError as exc:
            self.closed = True
            raise BarUnavailable(f"send failed ({exc})") from exc

    def _parse_frame(self) -> Optional[Tuple[bool, int, bytes]]:
        """Pop one complete frame off the buffer, or None if it isn't all here.

        Never consumes a partial frame, so a read timeout can't desync us.
        """
        buf = self._buf
        if len(buf) < 2:
            return None
        n = buf[1] & 0x7F
        pos = 2
        if n == 126:
            if len(buf) < 4:
                return None
            n = struct.unpack_from("!H", buf, 2)[0]
            pos = 4
        elif n == 127:
            if len(buf) < 10:
                return None
            n = struct.unpack_from("!Q", buf, 2)[0]
            pos = 10
        if n > _MAX_MESSAGE:
            raise BarError("message too large")
        key = b""
        if buf[1] & 0x80:
            if len(buf) < pos + 4:
                return None
            key = buf[pos:pos + 4]
            pos += 4
        if len(buf) < pos + n:
            return None
        payload = buf[pos:pos + n]
        self._buf = buf[pos + n:]
        return bool(buf[0] & 0x80), buf[0] & 0x0F, _mask(payload, key) if key else payload

    def send_json(self, data: Dict[str, Any]) -> None:
        self._send_frame(_OP_TEXT, json.dumps(data).encode("utf-8"))

    def readable(self) -> bool:
        """Something (data, a ping, a close) is waiting — checked without blocking."""
        if self._buf:
            return True
        try:
            ready, _, _ = select.select([self._sock], [], [], 0)
        except (OSError, ValueError):
            return True  # let the next read surface the error
        return bool(ready)

    def recv_text(self, timeout: Optional[float]) -> Optional[str]:
        """Next text message; None on timeout. Raises BarUnavailable on close."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            frame = self._parse_frame()
            if frame is None:
                if self.closed:
                    raise BarUnavailable("connection closed")
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._sock.settimeout(remaining)
                try:
                    chunk = self._sock.recv(65536)
                except socket.timeout:
                    return None
                except OSError as exc:
                    self.closed = True
                    raise BarUnavailable(f"connection lost ({exc})") from exc
                if not chunk:
                    self.closed = True
                    raise BarUnavailable("hub closed the connection")
                self._buf += chunk
                continue
            fin, opcode, payload = frame
            if opcode == _OP_PING:
                self._send_frame(_OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue
            if opcode == _OP_CLOSE:
                if not self._closing:  # echo the close unless we started it
                    try:
                        self._send_frame(_OP_CLOSE, payload[:2])
                    except BarUnavailable:
                        pass
                self.closed = True
                raise BarUnavailable("hub closed the connection")
            if opcode in (_OP_TEXT, _OP_BINARY):
                self._frags = [payload]
            elif opcode == _OP_CONT and self._frags:
                self._frags.append(payload)
            else:
                continue
            if fin:
                data, self._frags = b"".join(self._frags), []
                return data.decode("utf-8", errors="replace")

    def recv_json(self, timeout: Optional[float]) -> Optional[Dict[str, Any]]:
        """Next JSON object; None on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            text = self.recv_text(remaining)
            if text is None:
                return None
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data

    def close(self, timeout: float = 0.5) -> None:
        """Close handshake, then drain, so the hub never sees a TCP reset."""
        if not self.closed:
            self._closing = True
            try:
                self._send_frame(_OP_CLOSE, struct.pack("!H", 1000))
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if self.recv_text(deadline - time.monotonic()) is None:
                        break
            except (BarUnavailable, BarError, OSError):
                pass
        self.closed = True
        try:
            self._sock.close()
        except OSError:
            pass


class BarClient:
    """One agent's connection to the island.

    ``ephemeral`` clients (hooks, scripts) can come and go per event without
    their session disappearing; long-lived ones (wrappers) own their session
    and it ends when they disconnect.
    """

    def __init__(
        self,
        agent: Optional[str] = None,
        session_id: Optional[str] = None,
        *,
        url: Optional[str] = None,
        token: Optional[str] = None,
        role: str = "agent",
        ephemeral: bool = True,
        timeout: float = 2.0,
        client: str = "dulus-bar-client",
    ):
        # Launched by `dulusbar wrap`? Then report into the wrapper's own row.
        self.agent = agent or os.environ.get("DULUS_BAR_AGENT") or "Agent"
        self.session_id = session_id or os.environ.get("DULUS_BAR_SESSION_ID") or uuid.uuid4().hex[:8]
        self.url = url or server_url()
        self.token = token
        self.role = role
        self.ephemeral = ephemeral
        self.timeout = timeout
        self.client = client
        self.welcome: Dict[str, Any] = {}
        self._conn: Optional[Connection] = None

    # --- lifecycle --------------------------------------------------------------
    def connect(self, *, wait_welcome: float = 0.0) -> "BarClient":
        self._conn = Connection(self.url, self.timeout)
        hello: Dict[str, Any] = {
            "type": "hello",
            "v": 2,
            "role": self.role,
            "ephemeral": self.ephemeral,
            "client": self.client,
            # v1 hubs treat this as a health probe instead of a fake agent.
            "agent": "_health",
            "session_id": "_health",
        }
        if self.token:
            hello["token"] = self.token
        self._conn.send_json(hello)
        if wait_welcome > 0:
            msg = self._conn.recv_json(wait_welcome)
            if msg and msg.get("type") == "error":
                raise BarError(str(msg.get("message") or msg.get("error") or "refused"))
            if msg and msg.get("type") == "welcome":
                self.welcome = msg
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "BarClient":
        if self._conn is None:
            self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def connection(self) -> Connection:
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        return self._conn

    # --- agent -> bar -------------------------------------------------------------
    def _drain(self) -> None:
        """Answer pings and notice a closed hub before sending; never blocks.

        Unsolicited messages read here are dropped: a client waiting for an
        answer is inside request_permission(), not sending events.
        """
        conn = self._conn
        while conn is not None and not conn.closed and conn.readable():
            if conn.recv_text(0.05) is None:
                break

    def event(self, event_type: str, payload: Optional[Dict[str, Any]] = None, *,
              session_id: Optional[str] = None, agent: Optional[str] = None,
              request_id: str = "") -> None:
        """Send one event. A long-lived client survives a hub restart: on a
        dead connection it reconnects once and resends."""
        msg: Dict[str, Any] = {
            "agent": agent or self.agent,
            "type": event_type,
            "session_id": session_id or self.session_id,
            "payload": payload or {},
            "ts": time.time(),
        }
        if request_id:
            msg["request_id"] = request_id
        try:
            self._drain()
            self.connection.send_json(msg)
        except BarUnavailable:
            self.close()
            self.connect()  # raises BarUnavailable when the hub is really gone
            self.connection.send_json(msg)

    def request_permission(self, tool: str, args: str = "", *, detail: str = "",
                           can_always: bool = False, always_label: str = "",
                           timeout: Optional[float] = None,
                           extra: Optional[Dict[str, Any]] = None) -> Optional[Decision]:
        """Show Allow / Deny on the island and wait for the answer.

        Returns None when there's no answer: the request was withdrawn or
        answered elsewhere, the hub went away, or ``timeout`` expired (the card
        is withdrawn then too).
        """
        request_id = uuid.uuid4().hex[:12]
        payload: Dict[str, Any] = {"tool": tool, "args": args}
        if detail:
            payload["detail"] = detail
        if can_always:
            payload["can_always"] = True
            payload["always_label"] = always_label
        payload.update(extra or {})
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            self.event("tool_request", payload, request_id=request_id)
            conn = self.connection
            while True:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self.cancel(request_id)
                    return None
                msg = conn.recv_json(remaining)
                if msg is None:
                    continue
                kind = msg.get("type")
                rid = msg.get("request_id")
                if kind == "decision" and (
                    rid == request_id or (not rid and msg.get("session_id") == self.session_id)
                ):
                    p = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
                    return Decision(
                        approved=bool(p.get("approved")),
                        always=bool(p.get("always")),
                        message=str(p.get("message") or ""),
                        request_id=request_id,
                    )
                if kind == "request_resolved" and rid == request_id:
                    return None
        except BarUnavailable:
            return None

    def cancel(self, request_id: str) -> None:
        try:
            self.connection.send_json({"type": "cancel", "request_id": request_id})
        except BarUnavailable:
            pass

    # --- control (needs the hub token from hub.json) -----------------------------------
    def _token(self) -> str:
        return self.token or str(read_hub_info().get("token") or "")

    def _control(self, kind: str) -> Dict[str, Any]:
        conn = self.connection
        conn.send_json({"type": kind, "token": self._token()})
        while True:
            msg = conn.recv_json(self.timeout)
            if msg is None:
                raise BarError(f"no reply to {kind!r} (is this an older Dulus Bar?)")
            if msg.get("type") == "error":
                raise BarError(str(msg.get("message") or msg.get("error") or "refused"))
            if msg.get("type") == kind:
                return msg

    def status(self) -> Dict[str, Any]:
        """Sessions + pending requests, straight from the hub."""
        return self._control("status")

    def shutdown(self) -> bool:
        """Ask the running island to quit."""
        return bool(self._control("shutdown").get("ok"))


def probe(url: Optional[str] = None, timeout: float = 0.6) -> Optional[Dict[str, Any]]:
    """Return the hub's welcome if a Dulus Bar answers, ``{}`` for a v1 hub,
    or None when nothing (or something else) is listening."""
    try:
        bar = BarClient(url=url, timeout=timeout, client="dulus-bar-probe")
        bar.connect(wait_welcome=timeout)
    except (BarUnavailable, BarError, OSError, ValueError):
        return None
    try:
        return bar.welcome
    finally:
        bar.close()


def send_event(agent: str, event_type: str, session_id: str,
               payload: Optional[Dict[str, Any]] = None, *, url: Optional[str] = None,
               timeout: float = 1.0) -> bool:
    """Fire-and-forget one event. False if no island is running."""
    try:
        with BarClient(agent, session_id, url=url, timeout=timeout) as bar:
            bar.event(event_type, payload)
        return True
    except (BarUnavailable, BarError, OSError):
        return False
