"""Floating Dynamic-Island overlay for Dulus Bar (Windows / macOS / Linux).

On a MacBook with a camera notch the island anchors to the notch and stays
visible across every Space and full-screen app. Everywhere else it floats at
top-center. It shows every connected AI agent, forwards Allow/Deny approvals,
and — for Dulus specifically — surfaces the active model and context usage.
"""

from __future__ import annotations

import os
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import native
from .agents import detect_installed, is_dulus, style_for
from .server import AgentEvent, AgentEventServer

APP_NAME = "Dulus Bar"
WS_PORT = 17372
DOCS_URL = "https://bitbucket.org/dulus-ai/dulus-bar"

# --- palette --------------------------------------------------------------
# Elevated surfaces sit clearly above any wallpaper OR a dark terminal behind.
PILL_BG = "#1b1b21"
PILL_BG_HOVER = "#24242c"
PILL_BORDER = "#3a3a44"
PILL_BORDER_HOVER = "#50505c"
BG = "#151519"  # panel / toast surface
BG_HOVER = "#212128"
BORDER = "#2c2c34"
BORDER_HOVER = "#3a3a44"
TEXT = "#f4f4f5"
TEXT_DIM = "#b4b4bd"
TEXT_FAINT = "#7a7a84"
GOOD = "#4ade80"
WARN = "#facc15"
INFO = "#60a5fa"
BAD = "#f87171"
IDLE = "#71717a"

ROW_HEIGHT = 46

# --- notch-style auto-hide (Qt overlay: Windows / Linux) ------------------
# Mirrors the macOS notch surface: the island rests as a slim "peek" tab flush
# with the top edge and reveals on hover over the top-center zone, on agent
# activity (briefly), or on a permission request (staying open until answered).
# Disable with DULUS_BAR_NO_AUTOHIDE=1 to keep the classic always-visible pill.
PEEK_WIDTH = 132
PEEK_HEIGHT = 6
HOT_ZONE_HEIGHT = 26   # px below the top edge that counts as "hovering the notch"
HOT_ZONE_PAD = 52      # px of horizontal slack on each side of the island
BRIEF_REVEAL_MS = 2600  # how long agent activity peeks the island open

STATUS_COLORS = {
    "running": GOOD,
    "waiting": WARN,
    "done": INFO,
    "error": BAD,
    "idle": IDLE,
}

# Native default font family; replaced at runtime by native.default_font_family().
FONT_FAMILY = "Segoe UI"


@dataclass
class Session:
    """A running agent session as tracked by the overlay."""

    agent: str
    session_id: str
    status: str = "idle"  # idle, running, waiting, done, error
    message: str = ""
    started: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    pid: Optional[int] = None
    terminal_hint: str = ""
    model: str = ""  # Dulus only
    ctx: str = ""  # Dulus only, e.g. "38%" or "12k/200k"

    def __hash__(self) -> int:
        return hash((self.agent, self.session_id))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Session) and (self.agent, self.session_id) == (
            other.agent,
            other.session_id,
        )


def _ctx_color(ctx: str) -> str:
    """Green/amber/red based on the leading percentage in a ctx string."""
    digits = ""
    for ch in ctx:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    if not digits:
        return TEXT_DIM
    pct = int(digits)
    if pct >= 85:
        return BAD
    if pct >= 60:
        return WARN
    return GOOD


def _esc(text: str) -> str:
    """Minimal HTML escape for the RichText toast title."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _elide(text: str, limit: int = 88) -> str:
    """Collapse newlines/whitespace to a single line and truncate with an
    ellipsis — keeps a long, minified tool call to one clean, readable row
    instead of the messy multi-line wrap it used to become."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _permission_call_text(tool: str, args: str) -> str:
    """Build ONE clean representation of a tool call. Guards against a client
    that stuffs (almost) the same string into both `tool` and `args` — so the
    same call is never rendered twice."""
    tool = (tool or "").strip()
    args = (args or "").strip()
    if not args:
        return tool or "Run a tool?"
    if not tool:
        return args
    if tool in args or args in tool:  # redundant → keep the fuller one
        return args if len(args) >= len(tool) else tool
    return f"{tool}  {args}"


class PillButton(QPushButton):
    """Rounded Allow/Deny button. `fg`/`border` let a button read as a light
    primary (white fill, dark text) or a dark secondary (subtle fill, light
    text, hairline border) — the same hierarchy as the macOS approval surface."""

    def __init__(
        self,
        text: str,
        color: str,
        parent: Optional[QWidget] = None,
        fg: str = "#08080a",
        border: str = "",
    ):
        super().__init__(text, parent)
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        edge = f"1px solid {border}" if border else "none"
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {color};
                color: {fg};
                border: {edge};
                border-radius: 14px;
                padding: 0 18px;
                font-family: "{FONT_FAMILY}";
                font-weight: 700;
                font-size: 11px;
            }}
            QPushButton:hover {{ background-color: {color}; padding: 0 19px; }}
            QPushButton:pressed {{ background-color: {color}; }}
            """
        )


class SessionRow(QWidget):
    """One agent row inside the expanded panel."""

    clicked = pyqtSignal(Session)

    def __init__(self, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self.style_ = style_for(session.agent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(ROW_HEIGHT)
        self.setObjectName("row")
        self.setStyleSheet(
            "#row { background: transparent; border-radius: 10px; }"
            f"#row:hover {{ background: {BG_HOVER}; }}"
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 4, 12, 4)
        root.setSpacing(10)

        self.dot = QLabel("●")
        self.dot.setFixedWidth(12)
        self.dot.setFont(QFont(FONT_FAMILY, 9))
        self.dot.setStyleSheet(f"color: {STATUS_COLORS.get(session.status, IDLE)};")
        root.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.setContentsMargins(0, 0, 0, 0)
        self.icon = QLabel(self.style_.emoji)
        self.icon.setFont(QFont(FONT_FAMILY, 11))
        top.addWidget(self.icon)
        self.name = QLabel(self.style_.display)
        self.name.setFont(QFont(FONT_FAMILY, 11, QFont.Weight.DemiBold))
        self.name.setStyleSheet(f"color: {TEXT};")
        top.addWidget(self.name)
        top.addStretch()
        self.time = QLabel(self._fmt_time(session.last_seen))
        self.time.setFont(QFont(FONT_FAMILY, 9))
        self.time.setStyleSheet(f"color: {TEXT_FAINT};")
        top.addWidget(self.time)
        text_col.addLayout(top)

        self.sub = QLabel(self._subtitle(session))
        self.sub.setFont(QFont(FONT_FAMILY, 9))
        self.sub.setStyleSheet(f"color: {TEXT_DIM};")
        self.sub.setTextFormat(Qt.TextFormat.RichText)
        text_col.addWidget(self.sub)

        root.addLayout(text_col, 1)

    def _fmt_time(self, dt: datetime) -> str:
        elapsed = (datetime.now() - dt).total_seconds()
        if elapsed < 60:
            return f"{int(elapsed)}s"
        if elapsed < 3600:
            return f"{int(elapsed // 60)}m"
        return f"{int(elapsed // 3600)}h"

    def _subtitle(self, session: Session) -> str:
        msg = (session.message or session.status).strip()
        if is_dulus(session.agent) and (session.model or session.ctx):
            bits = []
            if session.model:
                bits.append(f"<span style='color:{self.style_.accent};'>{session.model}</span>")
            if session.ctx:
                bits.append(f"<span style='color:{_ctx_color(session.ctx)};'>ctx {session.ctx}</span>")
            prefix = " · ".join(bits)
            if msg:
                return f"{prefix} · <span style='color:{TEXT_FAINT};'>{msg[:48]}</span>"
            return prefix
        return f"<span style='color:{TEXT_DIM};'>{msg[:60]}</span>"

    def mousePressEvent(self, a0: Optional[QtGui.QMouseEvent]) -> None:
        self.clicked.emit(self.session)


class IslandPill(QWidget):
    """Collapsed island pill that toggles the expanded panel."""

    toggled = pyqtSignal()
    rightClicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        # QWidget *subclasses* need WA_StyledBackground for a stylesheet
        # background to actually paint (a plain QWidget does it automatically).
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(42)
        self.setMinimumWidth(160)
        self.setObjectName("pill")
        self._hovered = False
        self._apply_style()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _apply_style(self) -> None:
        bg = PILL_BG_HOVER if self._hovered else PILL_BG
        border = PILL_BORDER_HOVER if self._hovered else PILL_BORDER
        self.setStyleSheet(
            f"#pill {{ background-color: {bg}; border: 1px solid {border}; border-radius: 21px; }}"
        )

    def enterEvent(self, event: Optional[QtCore.QEvent]) -> None:
        self._hovered = True
        self._apply_style()

    def leaveEvent(self, a0: Optional[QtCore.QEvent]) -> None:
        self._hovered = False
        self._apply_style()

    def mousePressEvent(self, a0: Optional[QtGui.QMouseEvent]) -> None:
        if a0 is not None and a0.button() == Qt.MouseButton.RightButton:
            self.rightClicked.emit()
        else:
            self.toggled.emit()


class DulusBarOverlay(QMainWindow):
    """Top-center / notch-anchored floating island."""

    event_received = pyqtSignal(AgentEvent)
    permission_decision = pyqtSignal(str, str, bool)  # agent, session_id, approved

    def __init__(self, server: AgentEventServer):
        super().__init__()
        self.server = server
        self.server.on_event(self._on_agent_event)
        self.event_received.connect(self._handle_event, Qt.ConnectionType.QueuedConnection)  # type: ignore[call-arg]
        self.permission_decision.connect(self._broadcast_decision, Qt.ConnectionType.QueuedConnection)  # type: ignore[call-arg]

        self.sessions: Dict[tuple, Session] = {}
        self.pending_permissions: List[AgentEvent] = []
        self.expanded = False
        self.toast_visible = False
        self.current_permission: Optional[AgentEvent] = None
        self._configured_native = False

        # Notch-style auto-hide. On by default (Windows/Linux Qt overlay); the
        # native macOS surface handles its own notch behaviour separately.
        self._autohide = os.environ.get("DULUS_BAR_NO_AUTOHIDE") not in ("1", "true", "True")
        self.revealed = not self._autohide  # tucked to a peek until hovered
        self.permission_pinned = False      # stay revealed while a prompt is open
        self._reveal_until = 0.0            # monotonic deadline for a brief peek

        self._init_window()
        self._init_ui()
        self._reposition()
        self._init_refresh_timer()
        self._init_hover_timer()
        if self._autohide:
            self._apply_geometry()  # start tucked

    # --- setup ----------------------------------------------------------
    def _init_window(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setFixedHeight(58)

    def _init_ui(self) -> None:
        central = QWidget()
        central.setStyleSheet("background: transparent;")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- collapsed pill ---
        self.pill = IslandPill()
        self.pill.toggled.connect(self._toggle_expand)
        self.pill.rightClicked.connect(self._show_context_menu)
        pill_row = QHBoxLayout(self.pill)
        pill_row.setContentsMargins(16, 0, 16, 0)
        pill_row.setSpacing(8)

        self.icon_label = QLabel()
        self.icon_label.setFont(QFont(FONT_FAMILY, 13))
        self._bird_pixmap = self._load_bird_pixmap(20)
        self._apply_pill_icon(None)  # brand bird by default
        pill_row.addWidget(self.icon_label)

        self.status_label = QLabel(APP_NAME)
        self.status_label.setFont(QFont(FONT_FAMILY, 11, QFont.Weight.DemiBold))
        self.status_label.setStyleSheet(f"color: {TEXT};")
        pill_row.addWidget(self.status_label)

        self.meta_label = QLabel("")  # model / ctx (Dulus) or short status
        self.meta_label.setFont(QFont(FONT_FAMILY, 9))
        self.meta_label.setTextFormat(Qt.TextFormat.RichText)
        self.meta_label.setStyleSheet(f"color: {TEXT_DIM};")
        pill_row.addWidget(self.meta_label)

        pill_row.addStretch()

        self.count_badge = QLabel("")
        self.count_badge.setFont(QFont(FONT_FAMILY, 9, QFont.Weight.Bold))
        self.count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_badge.setFixedSize(20, 20)
        self.count_badge.setStyleSheet(f"color: #08080a; background: {GOOD}; border-radius: 10px;")
        self.count_badge.setVisible(False)
        pill_row.addWidget(self.count_badge)

        # --- expanded panel ---
        self.panel = QWidget()
        self.panel.setObjectName("panel")
        self.panel.setStyleSheet(
            f"#panel {{ background-color: {BG}; border: 1px solid {BORDER}; border-radius: 18px; }}"
        )
        self.panel.setFixedWidth(340)
        self.panel.setVisible(False)
        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setContentsMargins(12, 12, 12, 12)
        self.panel_layout.setSpacing(4)

        header = QLabel("ACTIVE AGENTS")
        header.setFont(QFont(FONT_FAMILY, 8, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {TEXT_FAINT}; letter-spacing: 1.5px;")
        self.panel_layout.addWidget(header)

        self.empty_label = QLabel("No agents connected yet.")
        self.empty_label.setFont(QFont(FONT_FAMILY, 10))
        self.empty_label.setStyleSheet(f"color: {TEXT_FAINT}; padding: 12px 4px;")
        self.panel_layout.addWidget(self.empty_label)

        self.sessions_container = QVBoxLayout()
        self.sessions_container.setSpacing(2)
        self.sessions_container.setContentsMargins(0, 0, 0, 0)
        self.panel_layout.addLayout(self.sessions_container)
        self.panel_layout.addStretch()

        # --- collapsed "peek" tab (notch rest state) ---
        # A slim bar flush with the top edge. When tucked this is all that shows;
        # hovering the top-center zone reveals the full pill.
        self.peek = QWidget()
        self.peek.setObjectName("peek")
        self.peek.setFixedSize(PEEK_WIDTH, PEEK_HEIGHT)
        self.peek.setVisible(False)
        self._style_peek(PILL_BORDER)

        # --- permission toast ---
        self._build_toast()

        # assemble
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.peek, alignment=Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.pill, alignment=Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.panel, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(container)

        shadow = QGraphicsDropShadowEffect(self.pill)
        shadow.setBlurRadius(34)
        shadow.setColor(QColor(0, 0, 0, 210))
        shadow.setOffset(0, 8)
        self.pill.setGraphicsEffect(shadow)

    def _build_toast(self) -> None:
        # Top-level window, NOT a child of the island. A child is clipped to the
        # tiny island window and never shows — which is why Allow/Deny was
        # invisible. As its own frameless, always-on-top window the permission
        # prompt floats below the pill anywhere on screen.
        self.toast = QWidget(None)
        self.toast.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        # The WINDOW is translucent ONLY so the corners round off and the shadow
        # can bleed. The solid bubble is an inner "card" child — a translucent
        # top-level's OWN stylesheet fill is unreliable (Qt skips it, leaving the
        # text floating on whatever's behind → invisible on a light/white
        # desktop). A normal child widget with WA_StyledBackground ALWAYS paints
        # its background — the same approach the expanded panel uses.
        self.toast.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.toast.setFixedSize(388, 132)
        self.toast.setVisible(False)

        outer = QVBoxLayout(self.toast)
        outer.setContentsMargins(14, 8, 14, 16)  # breathing room for the shadow

        card = QWidget(self.toast)
        card.setObjectName("toastcard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(
            f"#toastcard {{ background-color: {PILL_BG}; border: 1px solid {PILL_BORDER};"
            f" border-radius: 18px; }}"
        )
        outer.addWidget(card)

        t = QVBoxLayout(card)
        t.setContentsMargins(16, 12, 16, 12)
        t.setSpacing(6)

        self.toast_title = QLabel("Permission request")
        self.toast_title.setFont(QFont(FONT_FAMILY, 11))
        self.toast_title.setTextFormat(Qt.TextFormat.RichText)
        self.toast_title.setStyleSheet(f"color: {TEXT}; background: transparent;")
        t.addWidget(self.toast_title)

        self.toast_body = QLabel("")
        self.toast_body.setFont(QFont(FONT_FAMILY, 9))
        self.toast_body.setStyleSheet(f"color: {TEXT_DIM}; background: transparent;")
        self.toast_body.setWordWrap(True)
        t.addWidget(self.toast_body)

        t.addStretch(1)  # pin the buttons to the bottom, whatever the body height

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch()
        # macOS-style hierarchy: Allow is the light primary, Deny the dark
        # secondary — calmer and clearer than the old red/green pair.
        self.deny_btn = PillButton("Deny", "#2b2b33", fg=TEXT, border=BORDER_HOVER)
        self.allow_btn = PillButton("Allow", "#f5f5f6", fg="#08080a")
        btns.addWidget(self.deny_btn)
        btns.addWidget(self.allow_btn)
        t.addLayout(btns)

        self.deny_btn.clicked.connect(lambda: self._resolve_permission(False))
        self.allow_btn.clicked.connect(lambda: self._resolve_permission(True))

        toast_shadow = QGraphicsDropShadowEffect(card)
        toast_shadow.setBlurRadius(24)
        toast_shadow.setColor(QColor(0, 0, 0, 200))
        toast_shadow.setOffset(0, 6)
        card.setGraphicsEffect(toast_shadow)

    def _init_refresh_timer(self) -> None:
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_ui)
        self.timer.start(1000)

    # --- positioning (notch-aware) --------------------------------------
    def _reposition(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.geometry()
        width = self.width()
        x = geo.x() + (geo.width() - width) // 2

        if self._autohide and not self.revealed:
            # Tucked: peek sits flush against the very top edge, like a notch nub.
            y = geo.y()
            self.move(x, y)
            return

        notch = native.notch_geometry()
        if notch is not None:
            # Qt fallback: hang directly under the camera cutout. The native
            # Swift/AppKit surface is preferred on macOS for true integration.
            y = geo.y() + max(0, notch.height - 4)
        else:
            y = geo.y() + 8
        self.move(x, y)

    def showEvent(self, a0) -> None:  # noqa: ANN001
        super().showEvent(a0)
        # macOS: promote to an always-visible, all-Spaces window once shown.
        if not self._configured_native:
            self._configured_native = True
            try:
                native.configure_always_visible(self)
            except Exception:
                pass
            # Debug aid: auto-expand for screenshots (DULUS_BAR_AUTO_EXPAND=1).
            import os

            if os.environ.get("DULUS_BAR_AUTO_EXPAND") and not self.expanded:
                QTimer.singleShot(900, self._toggle_expand)
        self._reposition()

    # --- server plumbing ------------------------------------------------
    def _on_agent_event(self, event: AgentEvent) -> None:
        self.event_received.emit(event)

    def _broadcast_decision(self, agent: str, session_id: str, approved: bool) -> None:
        self.server.broadcast(
            {
                "agent": agent,
                "type": "decision",
                "session_id": session_id,
                "payload": {"approved": approved},
            }
        )

    def _handle_event(self, event: AgentEvent) -> None:
        # Ignore health pings / system probes — they are NOT agents.
        if event.event_type in ("ping", "health", "pong"):
            return
        if event.agent in ("_health", "VibeHealth", "DulusHealth", "health", ""):
            return
        if event.session_id in ("_health", "health"):
            return

        key = (event.agent, event.session_id)
        now = datetime.now()
        payload = event.payload or {}

        if event.event_type == "session_started":
            self.sessions[key] = Session(
                agent=event.agent,
                session_id=event.session_id,
                status="running",
                message="started",
                started=now,
                last_seen=now,
                pid=payload.get("pid"),
                terminal_hint=payload.get("terminal_hint", ""),
                model=payload.get("model", ""),
                ctx=payload.get("ctx", ""),
            )
        elif key in self.sessions:
            session = self.sessions[key]
            session.last_seen = now
            if payload.get("model"):
                session.model = payload["model"]
            if payload.get("ctx"):
                session.ctx = payload["ctx"]
            if event.event_type == "message":
                session.message = str(payload.get("text", ""))[:80]
                session.status = "running"
            elif event.event_type == "tool_request":
                session.status = "waiting"
                session.message = payload.get("tool", "permission request")
                self._show_permission(event)
            elif event.event_type == "tool_approved":
                session.status = "running"
                session.message = "approved"
            elif event.event_type == "tool_denied":
                session.status = "running"
                session.message = "denied"
            elif event.event_type == "completed":
                session.status = "done"
                session.message = "done"
            elif event.event_type == "error":
                session.status = "error"
                session.message = str(payload.get("text", "error"))
        else:
            self.sessions[key] = Session(
                agent=event.agent,
                session_id=event.session_id,
                status="running",
                message=str(payload.get("text", "")),
                last_seen=now,
                model=payload.get("model", ""),
                ctx=payload.get("ctx", ""),
            )

        # Agent activity peeks the island open briefly (permission requests pin
        # it open via _show_permission, so don't override that here).
        if event.event_type != "tool_request":
            self._reveal(brief=True)
        self._refresh_ui()

    # --- permissions ----------------------------------------------------
    def _show_permission(self, event: AgentEvent) -> None:
        self.pending_permissions.append(event)
        # A permission request forces the island fully open and pins it there
        # until the user answers — same as the macOS notch surface.
        self._reveal(pin=True)
        if not self.toast_visible:
            self._render_next_permission()

    def _render_next_permission(self) -> None:
        if not self.pending_permissions:
            self.toast.setVisible(False)
            self.toast_visible = False
            self.current_permission = None
            self.permission_pinned = False  # free the island to tuck again
            return

        event = self.pending_permissions[0]
        self.current_permission = event
        st = style_for(event.agent)
        payload = event.payload or {}
        # Title mirrors the macOS surface: agent name + model, falling back to a
        # faint "needs approval" when the model isn't known yet.
        sess = self.sessions.get((event.agent, event.session_id))
        model = (sess.model if sess else "") or payload.get("model", "")
        tag = _esc(model) if model else "needs approval"
        self.toast_title.setText(
            f"{st.emoji}&nbsp; <b>{_esc(st.display)}</b>"
            f"&nbsp;&nbsp;<span style='color:{TEXT_FAINT};'>{tag}</span>"
        )
        # ONE clean, elided line for the call — no duplicated text, no messy wrap.
        self.toast_body.setText(
            _elide(_permission_call_text(payload.get("tool", ""), payload.get("args", "")))
        )
        self.toast.setVisible(True)
        self.toast.raise_()
        self.toast_visible = True
        self._position_toast()

    def _resolve_permission(self, approved: bool) -> None:
        if not self.pending_permissions:
            return
        event = self.pending_permissions.pop(0)
        self.permission_decision.emit(event.agent, event.session_id, approved)
        key = (event.agent, event.session_id)
        if key in self.sessions:
            self.sessions[key].status = "running"
            self.sessions[key].message = "approved" if approved else "denied"
        self._render_next_permission()
        self._refresh_ui()

    def _position_toast(self) -> None:
        geo = self.geometry()
        toast_x = geo.x() + (geo.width() - self.toast.width()) // 2
        toast_y = geo.y() + self.pill.height() + 12
        if self.expanded and self.panel.isVisible():
            toast_y += self.panel.height() + 8
        self.toast.move(max(0, toast_x), toast_y)

    # --- expand / collapse ----------------------------------------------
    def _panel_height(self) -> int:
        n = len(self.sessions)
        if n == 0:
            body = 44  # empty-state label
        else:
            body = n * ROW_HEIGHT + (n - 1) * 2
        # margins(12+12) + header(16) + gap(6) + body
        return 12 + 16 + 6 + body + 12

    def _apply_geometry(self) -> None:
        # Tucked: show only the slim peek tab flush with the top edge.
        if self._autohide and not self.revealed:
            self.peek.setVisible(True)
            self.pill.setVisible(False)
            self.panel.setVisible(False)
            self.setFixedHeight(PEEK_HEIGHT + 6)
            self.setFixedWidth(PEEK_WIDTH + 8)
            self._reposition()
            self._position_toast()
            return

        # Revealed: full pill (optionally with the expanded panel).
        self.peek.setVisible(False)
        self.pill.setVisible(True)
        if self.expanded:
            ph = self._panel_height()
            self.panel.setFixedHeight(ph)
            # top(8) + pill(42) + gap(8) + panel + bottom shadow room(16)
            self.setFixedHeight(8 + 42 + 8 + ph + 16)
            self.setFixedWidth(360)
        else:
            self.setFixedHeight(58)
            self.setFixedWidth(max(self.pill.minimumWidth(), self.pill.sizeHint().width() + 8))
        self._reposition()
        self._position_toast()

    def _toggle_expand(self) -> None:
        self.expanded = not self.expanded
        self.panel.setVisible(self.expanded)
        if self.expanded:
            self._rebuild_rows()
        self._apply_geometry()
        self._refresh_ui()

    # --- brand icon -----------------------------------------------------
    def _load_bird_pixmap(self, px: int) -> Optional[QtGui.QPixmap]:
        """Load the Dulus bird, crisp on HiDPI (devicePixelRatio-aware)."""
        path = Path(__file__).resolve().parent / "assets" / "dulus-bird.png"
        if not path.is_file():
            return None
        src = QtGui.QPixmap(str(path))
        if src.isNull():
            return None
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        pm = src.scaledToHeight(
            max(1, int(px * dpr)), Qt.TransformationMode.SmoothTransformation
        )
        pm.setDevicePixelRatio(dpr)
        return pm

    def _apply_pill_icon(self, agent: Optional[str]) -> None:
        """Brand bird for Dulus / idle; the agent's own emoji for everyone else."""
        if (agent is None or is_dulus(agent)) and self._bird_pixmap is not None:
            self.icon_label.setPixmap(self._bird_pixmap)
        else:
            self.icon_label.clear()
            self.icon_label.setText(style_for(agent).emoji if agent else "🦅")

    # --- notch auto-hide (reveal on hover / activity / permission) -------
    def _style_peek(self, color: str) -> None:
        radius = PEEK_HEIGHT // 2
        self.peek.setStyleSheet(
            f"#peek {{ background-color: {color}; border-radius: {radius}px; }}"
        )

    def _refresh_peek(self) -> None:
        """Tint the resting peek so activity is visible without revealing."""
        statuses = [s.status for s in self.sessions.values()]
        if "waiting" in statuses:
            color = WARN
        elif "error" in statuses:
            color = BAD
        elif "running" in statuses:
            color = GOOD
        else:
            color = PILL_BORDER
        self._style_peek(color)

    def _init_hover_timer(self) -> None:
        self._hover_timer = QTimer(self)
        self._hover_timer.timeout.connect(self._poll_cursor)
        self._hover_timer.start(120)

    def _hot_zone(self) -> QtCore.QRect:
        """Top-center strip that behaves like the macOS notch hover target."""
        screen = QApplication.primaryScreen()
        geo = screen.geometry() if screen else QtCore.QRect(0, 0, 1920, 1080)
        w = max(self.width(), PEEK_WIDTH) + 2 * HOT_ZONE_PAD
        x = geo.x() + (geo.width() - w) // 2
        return QtCore.QRect(x, geo.y(), w, HOT_ZONE_HEIGHT)

    def _cursor_near(self) -> bool:
        pos = QtGui.QCursor.pos()
        if self._hot_zone().contains(pos):
            return True
        # Once revealed, hovering anywhere over the island keeps it open.
        return self.revealed and self.geometry().contains(pos)

    def _poll_cursor(self) -> None:
        if not self._autohide:
            return
        if self._cursor_near():
            self._reveal()
        else:
            self._maybe_tuck()

    def _reveal(self, *, brief: bool = False, pin: bool = False) -> None:
        if pin:
            self.permission_pinned = True
        if brief:
            self._reveal_until = time.monotonic() + BRIEF_REVEAL_MS / 1000.0
        if self._autohide and not self.revealed:
            self.revealed = True
            self._apply_geometry()
            self._refresh_ui()

    def _maybe_tuck(self) -> None:
        if not self._autohide or not self.revealed:
            return
        # Never tuck while the user is engaged or a decision is pending.
        if self.expanded or self.permission_pinned or self.toast_visible:
            return
        if time.monotonic() < self._reveal_until:
            return
        if self._cursor_near():
            return
        self.revealed = False
        self._apply_geometry()

    # --- render ---------------------------------------------------------
    def _foremost(self) -> Optional[Session]:
        pool = [s for s in self.sessions.values() if s.status in ("running", "waiting")]
        pool = pool or list(self.sessions.values())
        if not pool:
            return None
        waiting = [s for s in pool if s.status == "waiting"]
        pool = waiting or pool
        return sorted(pool, key=lambda s: s.last_seen, reverse=True)[0]

    def _refresh_ui(self) -> None:
        active = [s for s in self.sessions.values() if s.status in ("running", "waiting")]
        front = self._foremost()

        if front is not None:
            st = style_for(front.agent)
            self._apply_pill_icon(front.agent)
            self.status_label.setText(st.display)
            self.meta_label.setText(self._pill_meta(front))
        else:
            self._apply_pill_icon(None)
            self.status_label.setText(APP_NAME)
            self.meta_label.setText("")

        if active:
            self.count_badge.setText(str(len(active)))
            self.count_badge.setVisible(True)
        else:
            self.count_badge.setVisible(False)

        self.icon_label.adjustSize()
        self._prune()
        self._refresh_peek()
        if self.expanded:
            self._rebuild_rows()
        self._apply_geometry()

    def _pill_meta(self, session: Session) -> str:
        if is_dulus(session.agent) and (session.model or session.ctx):
            st = style_for(session.agent)
            bits = []
            if session.model:
                bits.append(f"<span style='color:{st.accent};'>{session.model}</span>")
            if session.ctx:
                bits.append(f"<span style='color:{_ctx_color(session.ctx)};'>ctx {session.ctx}</span>")
            return " · ".join(bits)
        msg = (session.message or session.status).strip()
        return f"<span style='color:{TEXT_FAINT};'>{msg[:32]}</span>"

    def _rebuild_rows(self) -> None:
        while self.sessions_container.count():
            item = self.sessions_container.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()

        ordered = sorted(self.sessions.values(), key=lambda s: s.last_seen, reverse=True)
        self.empty_label.setVisible(not ordered)
        for session in ordered:
            row = SessionRow(session)
            row.clicked.connect(self._on_session_click)
            self.sessions_container.addWidget(row)

    def _prune(self) -> None:
        cutoff = datetime.now().timestamp() - 1800
        ghosts = {"_health", "VibeHealth", "DulusHealth", "health"}
        self.sessions = {
            k: v
            for k, v in self.sessions.items()
            if v.last_seen.timestamp() > cutoff
            and v.agent not in ghosts
            and v.session_id not in ("_health", "health")
        }

    def _on_session_click(self, session: Session) -> None:
        hint = session.terminal_hint
        if hint:
            w = native.find_window_by_title(hint)
            if w and native.activate_window(w.handle):
                return
        native.jump_to_terminal(hint)

    # --- launch agents (right-click menu) -------------------------------
    def _show_context_menu(self) -> None:
        menu = QtWidgets.QMenu()
        menu.setStyleSheet(
            f"QMenu {{ background: {BG}; color: {TEXT}; border: 1px solid {BORDER}; "
            f"border-radius: 10px; padding: 6px; font-size: 12px; }}"
            f"QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}"
            f"QMenu::item:selected {{ background: {BG_HOVER}; }}"
            f"QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 8px; }}"
        )

        open_agent = menu.addAction("📂  Open agent…")
        open_agent.triggered.connect(self._open_agent_dialog)

        if (self._repo_root() / "wrappers" / "dulus_wrapper.py").exists():
            open_dulus = menu.addAction("🦅  Open Dulus")
            open_dulus.triggered.connect(self._open_dulus)

        installed = detect_installed()
        if installed:
            menu.addSeparator()
            for st, exe in installed:
                act = menu.addAction(f"{st.emoji}  Open {st.display}")
                act.triggered.connect(lambda _=False, n=st.display, e=exe: self._launch_agent(n, [e]))

        menu.addSeparator()
        toggle = menu.addAction("Collapse" if self.expanded else "Expand")
        toggle.triggered.connect(self._toggle_expand)
        quit_act = menu.addAction("Quit Dulus Bar")
        quit_act.triggered.connect(QApplication.quit)

        menu.exec(QtGui.QCursor.pos())

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def _python_console(self) -> str:
        """A python with a real console (not pythonw, which has no stdin)."""
        exe = sys.executable or "python"
        low = exe.lower()
        if low.endswith("pythonw.exe"):
            candidate = exe[:-len("pythonw.exe")] + "python.exe"
            if Path(candidate).exists():
                return candidate
        import shutil

        return exe if not low.endswith("pythonw.exe") else (shutil.which("python") or "python")

    def _launch_agent(self, display_name: str, command: List[str]) -> None:
        root = self._repo_root()
        wrapper = root / "wrappers" / "agent_wrapper.py"
        argv = [self._python_console(), str(wrapper), display_name, *command]
        ok = native.open_terminal_running(argv, title=display_name, cwd=str(root))
        if not ok:
            QtWidgets.QMessageBox.warning(
                self, "Dulus Bar",
                f"Couldn't open a terminal for {display_name}.\n"
                "Run it manually:\n  " + " ".join(argv),
            )

    def _open_dulus(self) -> None:
        root = self._repo_root()
        wrapper = root / "wrappers" / "dulus_wrapper.py"
        argv = [self._python_console(), str(wrapper)]
        ok = native.open_terminal_running(argv, title="Dulus", cwd=str(root))
        if not ok:
            QtWidgets.QMessageBox.warning(
                self, "Dulus Bar", "Couldn't open a terminal for Dulus.\nRun: " + " ".join(argv)
            )

    def _open_agent_dialog(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open agent",
            str(Path.home()),
            "Agents (*.py *.exe *.sh *.js *.mjs *.ts);;All files (*)",
        )
        if not path:
            return
        p = Path(path)
        guess = style_for(p.stem).display
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Agent name", "Name shown on the bar:", text=guess
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        if p.suffix.lower() == ".py":
            command = [self._python_console(), str(p)]
        elif p.suffix.lower() in (".js", ".mjs", ".ts"):
            import shutil

            node = shutil.which("node") or "node"
            command = [node, str(p)]
        else:
            command = [str(p)]
        self._launch_agent(name, command)

    def closeEvent(self, a0: Optional[QtGui.QCloseEvent]) -> None:
        if a0 is not None:
            a0.ignore()
        self.hide()


class TrayApp:
    """System-tray icon + overlay launcher."""

    def __init__(self, app: QApplication, overlay: DulusBarOverlay):
        self.app = app
        self.overlay = overlay
        self.tray: Optional[QtWidgets.QSystemTrayIcon] = None

    def build(self) -> None:
        brand_icon = Path(__file__).resolve().parent / "assets" / "dulus-bird.png"
        icon = QIcon(str(brand_icon)) if brand_icon.is_file() else QIcon()
        if icon.isNull():
            icon = QIcon.fromTheme("utilities-terminal")
        if icon.isNull():
            pixmap = QtGui.QPixmap(64, 64)
            pixmap.fill(QColor(BG))
            icon = QIcon(pixmap)

        self.app.setWindowIcon(icon)

        self.tray = QtWidgets.QSystemTrayIcon(self.app)
        self.tray.setIcon(icon)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(self._on_tray_click)

        ov = self.overlay
        menu = QtWidgets.QMenu()
        menu.setStyleSheet(
            f"QMenu {{ background: {BG}; color: {TEXT}; border: 1px solid {BORDER}; "
            f"border-radius: 10px; padding: 6px; font-size: 12px; }}"
            f"QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}"
            f"QMenu::item:selected {{ background: {BG_HOVER}; }}"
            f"QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 8px; }}"
        )

        show_action = menu.addAction("Show island")
        if show_action is not None:
            show_action.triggered.connect(ov.show)

        # Open Dulus — only if the wrapper ships alongside (matches macOS menu).
        if (ov._repo_root() / "wrappers" / "dulus_wrapper.py").exists():
            open_dulus = menu.addAction("🦅  Open Dulus")
            if open_dulus is not None:
                open_dulus.triggered.connect(ov._open_dulus)

        # Open agent… — submenu of detected agents + "choose any", like macOS.
        agent_menu = menu.addMenu("Open agent…")
        if agent_menu is not None:
            agent_menu.setStyleSheet(menu.styleSheet())
            installed = detect_installed()
            for st, exe in installed:
                act = agent_menu.addAction(f"{st.emoji}  {st.display}")
                if act is not None:
                    act.triggered.connect(
                        lambda _=False, n=st.display, e=exe: ov._launch_agent(n, [e])
                    )
            if installed:
                agent_menu.addSeparator()
            choose = agent_menu.addAction("Choose any AI or executable…")
            if choose is not None:
                choose.triggered.connect(ov._open_agent_dialog)

        folder = menu.addAction("Open Dulus Bar folder")
        if folder is not None:
            folder.triggered.connect(self._open_project_folder)

        open_docs = menu.addAction("Open docs")
        if open_docs is not None:
            open_docs.triggered.connect(lambda: webbrowser.open(DOCS_URL))

        menu.addSeparator()
        quit_action = menu.addAction("Quit Dulus Bar")
        if quit_action is not None:
            quit_action.triggered.connect(self.app.quit)

        self.tray.setContextMenu(menu)
        self.tray.show()

    def _open_project_folder(self) -> None:
        root = str(self.overlay._repo_root())
        try:
            if sys.platform == "win32":
                os.startfile(root)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                import subprocess

                subprocess.Popen(["open", root])
            else:
                import subprocess

                subprocess.Popen(["xdg-open", root])
        except Exception:
            pass

    def _on_tray_click(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick:
            self.overlay.show()


def run_overlay() -> None:
    import sys

    global FONT_FAMILY

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)

    FONT_FAMILY = native.default_font_family()
    app.setFont(QFont(FONT_FAMILY, 10))

    server = AgentEventServer(port=WS_PORT)
    server.start()

    overlay = DulusBarOverlay(server)
    overlay.show()

    tray = TrayApp(app, overlay)
    tray.build()

    sys.exit(app.exec())


if __name__ == "__main__":
    run_overlay()
