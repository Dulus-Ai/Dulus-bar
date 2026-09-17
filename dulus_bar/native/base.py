"""Backend interface + safe no-op fallback shared by every platform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class WindowInfo:
    """Lightweight, platform-agnostic descriptor of a top-level window."""

    handle: object  # hwnd (win), window id (x11), or app pid/name (mac)
    title: str
    pid: int = 0
    process_name: str = ""

    def __repr__(self) -> str:
        return f"WindowInfo({self.process_name or '?'}: {self.title!r})"


@dataclass
class NotchGeometry:
    """Camera-notch rectangle in global screen pixels (macOS)."""

    x: int
    y: int
    width: int
    height: int
    screen_width: int
    screen_height: int


# Terminal / editor process or title fragments we consider "jumpable".
TERMINAL_HINTS = (
    "windows terminal",
    "powershell",
    "pwsh",
    "cmd.exe",
    "conhost",
    "wt.exe",
    "alacritty",
    "wezterm",
    "kitty",
    "tabby",
    "hyper",
    "conemu",
    "cmder",
    "iterm",
    "terminal",
    "gnome-terminal",
    "konsole",
    "xterm",
    "warp",
    "ghostty",
    "cursor",
    "visual studio code",
    "code.exe",
    "code",
    "zed",
    "windsurf",
)


class NativeBackend:
    """Default backend: everything is a safe no-op.

    Real platforms subclass this and override what they can do. Anything a
    platform cannot do falls back to these no-ops instead of crashing.
    """

    name = "noop"

    # --- window discovery / focus ---------------------------------------
    def list_windows(self, only_visible: bool = True) -> List[WindowInfo]:
        return []

    def find_window_by_title(self, title_substring: str) -> Optional[WindowInfo]:
        needle = title_substring.lower().strip()
        if not needle:
            return None
        for w in self.list_windows():
            if needle in w.title.lower():
                return w
        return None

    def find_terminal_windows(self) -> List[WindowInfo]:
        out: List[WindowInfo] = []
        for w in self.list_windows():
            haystack = f"{w.title} {w.process_name}".lower()
            if any(h in haystack for h in TERMINAL_HINTS):
                out.append(w)
        return out

    def activate_window(self, handle: object) -> bool:
        return False

    def jump_to_terminal(self, title_hint: str = "") -> bool:
        if title_hint:
            w = self.find_window_by_title(title_hint)
            if w and self.activate_window(w.handle):
                return True
        for w in self.find_terminal_windows():
            if self.activate_window(w.handle):
                return True
        return False

    def open_terminal(self) -> bool:
        return False

    def open_terminal_running(self, command, title="", cwd=None) -> bool:
        """Open a NEW interactive terminal window running ``command``.

        ``command`` is an argv list. The agent gets a real console it can read
        stdin from, which is what lets the wrapper pipe approvals. Returns False
        if the platform can't spawn a terminal.
        """
        return False

    # --- overlay placement / behavior -----------------------------------
    def notch_geometry(self) -> Optional[NotchGeometry]:
        """Return the camera-notch rect, or None when the display has none."""
        return None

    def configure_always_visible(self, win: object) -> None:
        """Keep the overlay above the menu bar / on every Space (macOS)."""
        return None

    def default_font_family(self) -> str:
        return "sans-serif"

    def prepare_environment(self) -> None:
        """Adjust process env *before* the Qt application exists.

        Deliberately Qt-free so it can run at the very top of startup, where
        platform knobs such as the Qt platform plugin must still be settable.
        """
        return None

    def overlay_use_available_geometry(self) -> bool:
        """Anchor the island inside the work area instead of the raw screen.

        True on desktops that reserve screen edges for panels/docks, so the
        island doesn't end up hidden underneath a top bar.
        """
        return False

    def overlay_extra_window_flags(self) -> Tuple[str, ...]:
        """Extra ``Qt.WindowType`` member *names* to OR into the overlay flags.

        Names (not Qt objects) keep this module import-safe without Qt; unknown
        names are ignored by the caller.
        """
        return ()

    def overlay_settle_delays_ms(self) -> Tuple[int, ...]:
        """Delays at which the overlay re-asserts its position after mapping.

        Window managers routinely apply their own placement policy to a
        frameless window when it is first mapped or resized, so the overlay
        re-states where it belongs a few times while things settle.
        """
        return (0, 60)
