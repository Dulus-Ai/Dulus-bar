"""Wire protocol shared by the hub, the island UIs and every agent client.

Deliberately dependency-free (no Qt, no websockets) so hooks, wrappers and the
CLI can import it in a few milliseconds.

Protocol v1 — what every existing client speaks — is a stream of JSON events:

    {"agent": "Claude", "type": "tool_request", "session_id": "abc",
     "payload": {"tool": "Bash", "args": "rm -rf build/"}}

v2 is a strict superset. Clients *may* open with a ``hello`` (role, protocol
version, auth token for UIs), and events *may* carry a ``request_id`` (so an
Allow/Deny reaches exactly the request it answers) and a ``ts`` (so events
from short-lived hook processes can be ordered). See PROTOCOL.md.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

PROTOCOL_VERSION = 2
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17372

# --- event types ------------------------------------------------------------
# Agent -> bar (v1)
SESSION_STARTED = "session_started"
MESSAGE = "message"
TOOL_REQUEST = "tool_request"
TOOL_APPROVED = "tool_approved"
TOOL_DENIED = "tool_denied"
COMPLETED = "completed"
ERROR = "error"
# Agent -> bar (v2)
PROMPT = "prompt"              # the user submitted a prompt: the agent is working
TOOL_STARTED = "tool_started"  # a tool began running
TOOL_FINISHED = "tool_finished"
IDLE = "idle"                  # the agent is waiting for the user's next input
SESSION_ENDED = "session_ended"
CANCEL = "cancel"              # the agent withdrew one of its tool requests
# Hub -> UIs
DISCONNECTED = "disconnected"        # a long-lived agent socket dropped
SESSION_REMOVED = "session_removed"  # housekeeping: process gone / stale
REQUEST_RESOLVED = "request_resolved"
SNAPSHOT = "snapshot"
# UI -> hub -> agent
DECISION = "decision"
# Control
HELLO = "hello"
WELCOME = "welcome"
STATUS = "status"
SHUTDOWN = "shutdown"

ROLE_AGENT = "agent"
ROLE_UI = "ui"

# Health probes (connect.ps1, the Dulus app, older Vibe Island builds) are not
# agents and must never show up as a session.
_HEALTH_TYPES = frozenset({"ping", "health", "pong"})
_HEALTH_AGENTS = frozenset({"", "_health", "health", "VibeHealth", "DulusHealth"})
_HEALTH_SESSIONS = frozenset({"_health", "health"})


def is_health(agent: str, event_type: str, session_id: str) -> bool:
    return (
        event_type in _HEALTH_TYPES
        or (agent or "") in _HEALTH_AGENTS
        or session_id in _HEALTH_SESSIONS
    )


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def host_id() -> str:
    """Identifies the PID namespace a process lives in (WSL is not Windows)."""
    return f"{sys.platform}:{socket.gethostname()}"


class AgentEvent:
    """Normalized event from an agent client (or synthesized by the hub).

    A plain class rather than a dataclass on purpose: this module sits on the
    hook hot path, and importing dataclasses costs more than all of it.
    """

    __slots__ = ("agent", "event_type", "session_id", "payload", "id", "request_id", "ts")

    def __init__(self, agent: str, event_type: str, session_id: str, payload: Optional[Dict] = None,
                 id: str = "", request_id: str = "", ts: float = 0.0):
        self.agent = agent
        self.event_type = event_type
        self.session_id = session_id
        self.payload = payload if payload is not None else {}
        self.id = id or uuid.uuid4().hex[:8]
        self.request_id = request_id
        self.ts = ts  # wall-clock seconds; 0 means "when it arrived"

    def __repr__(self) -> str:
        return (f"AgentEvent(agent={self.agent!r}, event_type={self.event_type!r}, "
                f"session_id={self.session_id!r}, request_id={self.request_id!r}, payload={self.payload!r})")

    @classmethod
    def from_message(cls, data: Dict[str, Any]) -> "AgentEvent":
        payload = data.get("payload")
        try:
            ts = float(data.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        return cls(
            agent=str(data.get("agent") or "unknown"),
            event_type=str(data.get("type") or MESSAGE),
            session_id=str(data.get("session_id") or "default"),
            payload=payload if isinstance(payload, dict) else {},
            request_id=str(data.get("request_id") or ""),
            ts=ts,
        )

    def to_message(self) -> Dict[str, Any]:
        msg: Dict[str, Any] = {
            "agent": self.agent,
            "type": self.event_type,
            "session_id": self.session_id,
            "payload": self.payload,
        }
        if self.request_id:
            msg["request_id"] = self.request_id
        if self.ts:
            msg["ts"] = self.ts
        return msg


def make_event(agent: str, event_type: str, session_id: str, payload: Optional[Dict] = None,
               **extra: Any) -> Dict[str, Any]:
    """Build an agent -> bar message, stamped with the time it happened."""
    msg: Dict[str, Any] = {
        "agent": agent,
        "type": event_type,
        "session_id": session_id,
        "payload": payload or {},
        "ts": time.time(),
    }
    msg.update({k: v for k, v in extra.items() if v not in (None, "")})
    return msg


# --- where the running hub advertises itself ----------------------------------


def app_dir() -> Path:
    """Per-user directory for Dulus Bar settings and runtime state."""
    override = os.environ.get("DULUS_BAR_HOME")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return Path(base) / "Dulus Bar" if base else home / "AppData" / "Roaming" / "Dulus Bar"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Dulus Bar"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else home / ".config") / "dulus-bar"


def hub_info_path() -> Path:
    return app_dir() / "hub.json"


def configured_port() -> int:
    raw = os.environ.get("DULUS_BAR_PORT", "")
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


def new_token() -> str:
    import secrets  # not on the hook hot path

    return secrets.token_urlsafe(24)


def write_hub_info(port: int, token: str, version: str) -> Path:
    """Publish the hub's port + UI token, readable by this user only.

    Browsers can't read it (that's what keeps a web page from approving tool
    calls) and neither can other accounts on a shared machine.
    """
    path = hub_info_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    data = {"port": port, "token": token, "pid": os.getpid(), "version": version}
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)
    return path


def read_hub_info() -> Dict[str, Any]:
    try:
        data = json.loads(hub_info_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def clear_hub_info() -> None:
    """Remove hub.json, but only if it still describes *this* process."""
    if read_hub_info().get("pid") != os.getpid():
        return
    try:
        hub_info_path().unlink()
    except OSError:
        pass


def server_url() -> str:
    """WebSocket URL clients should dial."""
    explicit = os.environ.get("DULUS_BAR_SERVER") or os.environ.get("VIBE_ISLAND_SERVER")
    if explicit:
        return explicit
    if os.environ.get("DULUS_BAR_PORT"):
        port = configured_port()
    else:
        port = read_hub_info().get("port") or DEFAULT_PORT
    return f"ws://{DEFAULT_HOST}:{port}"
