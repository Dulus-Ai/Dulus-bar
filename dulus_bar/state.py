"""Session + permission state, shared by the hub and the Qt island.

Pure and Qt-free: it folds AgentEvents into sessions plus a FIFO of pending
permission requests. The hub keeps one copy (for snapshots, ``dulusbar
status`` and to know which requests an event implicitly answered); the Qt
island keeps its own copy fed by the very same event stream, so the two agree
without sharing mutable state across threads.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import protocol as P
from .protocol import AgentEvent
from .text import call_text, first_line, one_line  # noqa: F401  (re-exported)

Key = Tuple[str, str]

ACTIVE = ("running", "waiting")
# Attention first: a prompt you must answer, then failures, then live work.
_RANK = {"waiting": 0, "error": 1, "running": 2, "done": 3, "idle": 4}
_IGNORED = frozenset({P.DECISION, P.HELLO, P.WELCOME, P.SNAPSHOT, P.STATUS, P.SHUTDOWN, P.CANCEL})
_REMOVALS = frozenset({P.DISCONNECTED, P.SESSION_ENDED, P.SESSION_REMOVED})
_OUTCOME_WORDS = {"approved": "Approved", "denied": "Denied"}


def _from_dict(cls, data: Dict[str, Any]):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Permission:
    """A tool call waiting for Allow / Deny."""

    request_id: str
    agent: str
    session_id: str
    tool: str = ""
    args: str = ""
    detail: str = ""        # multi-line: the full command, a diff excerpt…
    can_always: bool = False
    always_label: str = ""  # what "Always allow" would allow, e.g. "Bash(npm test:*)"
    created: float = field(default_factory=time.time)

    @property
    def key(self) -> Key:
        return (self.agent, self.session_id)

    def label(self) -> str:
        return call_text(self.tool, self.args)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Permission":
        return _from_dict(cls, data)

    @classmethod
    def from_event(cls, event: AgentEvent, now: Optional[float] = None) -> "Permission":
        p = event.payload or {}
        return cls(
            request_id=event.request_id or str(p.get("request_id") or "") or P.new_request_id(),
            agent=event.agent,
            session_id=event.session_id,
            tool=one_line(p.get("tool"), 200),
            args=one_line(p.get("args"), 300),
            detail=str(p.get("detail") or "")[:4000],
            can_always=bool(p.get("can_always")),
            always_label=one_line(p.get("always_label"), 120),
            created=now or time.time(),
        )


@dataclass
class Session:
    """One agent session as the island shows it."""

    agent: str
    session_id: str
    status: str = "running"  # running, waiting, done, error, idle
    message: str = ""
    model: str = ""
    ctx: str = ""
    cwd: str = ""
    title: str = ""          # project / custom title shown next to the agent name
    pid: Optional[int] = None
    pid_host: str = ""
    terminal_hint: str = ""
    started: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    status_since: float = field(default_factory=time.time)
    last_ts: float = 0.0

    @property
    def key(self) -> Key:
        return (self.agent, self.session_id)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        return _from_dict(cls, data)


def _merge_meta(session: Session, p: Dict[str, Any]) -> None:
    for attr in ("model", "ctx", "cwd", "terminal_hint", "pid_host"):
        value = p.get(attr)
        if value not in (None, ""):
            setattr(session, attr, one_line(value, 200))
    title = p.get("title") or p.get("project")
    if title:
        session.title = one_line(title, 60)
    elif session.cwd and not session.title:
        session.title = os.path.basename(session.cwd.rstrip("/\\"))
    pid = p.get("pid")
    if isinstance(pid, bool):
        return
    if isinstance(pid, (int, float)) or (isinstance(pid, str) and pid.isdigit()):
        session.pid = int(pid) or None


class SessionStore:
    """Folds the event stream into sessions + a FIFO of pending permissions."""

    def __init__(self) -> None:
        self.sessions: Dict[Key, Session] = {}
        self.permissions: List[Permission] = []

    # --- queries -------------------------------------------------------------
    def get(self, agent: str, session_id: str) -> Optional[Session]:
        return self.sessions.get((agent, session_id))

    def pending_for(self, key: Key) -> List[Permission]:
        return [p for p in self.permissions if p.key == key]

    def find_permission(self, request_id: str) -> Optional[Permission]:
        for perm in self.permissions:
            if perm.request_id == request_id:
                return perm
        return None

    def current_permission(self) -> Optional[Permission]:
        return self.permissions[0] if self.permissions else None

    def active(self) -> List[Session]:
        return [s for s in self.sessions.values() if s.status in ACTIVE]

    def foremost(self) -> Optional[Session]:
        """The session the collapsed pill should talk about."""
        current = self.current_permission()
        if current is not None and current.key in self.sessions:
            return self.sessions[current.key]
        pool = self.active() or list(self.sessions.values())
        return max(pool, key=lambda s: s.updated) if pool else None

    def ordered(self) -> List[Session]:
        """Panel order: attention first, then newest within each status."""
        return sorted(self.sessions.values(), key=lambda s: (_RANK.get(s.status, 9), -s.started))

    # --- the reducer -----------------------------------------------------------
    def apply(self, event: AgentEvent) -> List[Permission]:
        """Apply one event; return permissions it implicitly answered.

        An agent that moves on (runs the tool, takes a new prompt, finishes its
        turn) has had its prompt answered somewhere else — usually right in the
        terminal — so the island must drop the card instead of leaving a stale
        Allow/Deny behind.
        """
        kind = event.event_type
        if kind in _IGNORED or P.is_health(event.agent, kind, event.session_id):
            return []
        key = (event.agent, event.session_id)
        p = event.payload or {}
        now = time.time()

        if kind == P.REQUEST_RESOLVED:
            self.resolve(event.request_id or str(p.get("request_id") or ""), str(p.get("outcome") or ""))
            return []
        if kind in _REMOVALS:
            return self.remove_session(key)

        session = self.sessions.get(key)
        if session is None:
            session = Session(agent=event.agent, session_id=event.session_id,
                              started=now, updated=now, status_since=now)
            self.sessions[key] = session
        ts = event.ts or now
        if kind != P.TOOL_REQUEST and session.last_ts and ts < session.last_ts:
            # A late event from a hook process that lost a race: never step back.
            return []
        session.last_ts = max(session.last_ts, ts)
        session.updated = now
        _merge_meta(session, p)

        text = one_line(p.get("text") or p.get("message") or "", 160)
        tool = one_line(p.get("tool"), 200)
        args = one_line(p.get("args"), 300)
        resolved: List[Permission] = []

        if kind == P.SESSION_STARTED:
            self._set(session, "running", text or "Started", now)
        elif kind == P.PROMPT:
            resolved = self._take(lambda perm: perm.key == key)
            self._set(session, "running", text or "Working…", now)
        elif kind == P.TOOL_STARTED:
            self._set(session, "running", call_text(tool, args, " · ") or "Working…", now)
        elif kind == P.TOOL_FINISHED:
            resolved = self._take(
                lambda perm: perm.key == key and perm.tool == tool and (not args or perm.args == args)
            )
            self._set(session, "running", call_text(tool, args, " · ") or session.message, now)
        elif kind == P.TOOL_REQUEST:
            perm = Permission.from_event(event, now)
            if self.find_permission(perm.request_id) is None:
                self.permissions.append(perm)
        elif kind in (P.TOOL_APPROVED, P.TOOL_DENIED):
            resolved = self._take(lambda perm: perm.key == key)
            self._set(session, "running", "Approved" if kind == P.TOOL_APPROVED else "Denied", now)
        elif kind in (P.IDLE, P.COMPLETED):
            resolved = self._take(lambda perm: perm.key == key)
            fallback = "Waiting for you" if kind == P.IDLE else "Done"
            self._set(session, "done", text or fallback, now)
        elif kind == P.ERROR:
            self._set(session, "error", text or "Error", now)
        elif kind == P.MESSAGE or text:
            self._set(session, "running", text or session.message, now)
        self._normalize(session, now)
        return resolved

    # --- mutations -------------------------------------------------------------
    def resolve(self, request_id: str, outcome: str = "") -> Optional[Permission]:
        """Remove one pending request (it was answered or withdrawn)."""
        for index, perm in enumerate(self.permissions):
            if perm.request_id == request_id:
                del self.permissions[index]
                session = self.sessions.get(perm.key)
                if session is not None:
                    self._normalize(session, time.time())
                    word = _OUTCOME_WORDS.get(outcome)
                    if word and not self.pending_for(perm.key):
                        session.message = word
                return perm
        return None

    def remove_session(self, key: Key) -> List[Permission]:
        self.sessions.pop(key, None)
        return self._take(lambda perm: perm.key == key)

    def finished(self) -> List[Key]:
        return [
            s.key for s in self.sessions.values()
            if s.status not in ACTIVE and not self.pending_for(s.key)
        ]

    def prune(self, now: float, max_idle: float,
              alive: Optional[Callable[[Session], Optional[bool]]] = None) -> List[Key]:
        """Drop sessions whose process is gone, or that went silent too long.

        ``alive`` answers True / False / None (unknown). A session we *know* is
        alive is never pruned for being quiet — an agent waiting on you for an
        hour is still there.
        """
        removed: List[Key] = []
        for key, session in list(self.sessions.items()):
            if self.pending_for(key):
                continue
            state = alive(session) if alive else None
            if state is False or (state is None and now - session.updated > max_idle):
                self.remove_session(key)
                removed.append(key)
        return removed

    def snapshot(self) -> Dict[str, Any]:
        return {
            "sessions": [s.to_dict() for s in self.ordered()],
            "permissions": [p.to_dict() for p in self.permissions],
        }

    def load(self, snapshot: Dict[str, Any]) -> None:
        sessions = [Session.from_dict(d) for d in snapshot.get("sessions") or [] if isinstance(d, dict)]
        self.sessions = {s.key: s for s in sessions}
        self.permissions = [
            Permission.from_dict(d) for d in snapshot.get("permissions") or [] if isinstance(d, dict)
        ]

    # --- internals ---------------------------------------------------------------
    def _take(self, predicate: Callable[[Permission], bool]) -> List[Permission]:
        taken = [p for p in self.permissions if predicate(p)]
        if taken:
            self.permissions = [p for p in self.permissions if not predicate(p)]
        return taken

    @staticmethod
    def _set(session: Session, status: str, message: str, now: float) -> None:
        if status != session.status:
            session.status_since = now
        session.status = status
        session.message = message

    def _normalize(self, session: Session, now: float) -> None:
        """A session is "waiting" exactly while it has a pending request."""
        pending = self.pending_for(session.key)
        if pending:
            if session.status != "waiting":
                session.status_since = now
            session.status = "waiting"
            session.message = pending[0].label() or "Needs approval"
        elif session.status == "waiting":
            session.status = "running"
            session.status_since = now
