"""Cross-platform native integration layer for Dulus Bar.

Exposes a single OS-appropriate backend so the rest of the app never has to
know whether it is running on Windows, macOS or Linux. Each backend knows how
to:

  * find and focus terminal / editor windows (jump-to-terminal)
  * report the camera-notch geometry (macOS) so the island can hug it
  * keep the overlay always-visible across Spaces / full-screen (macOS)
  * pick a native-looking default UI font

Importing this package is always safe: the heavy, OS-specific imports live
inside each backend module and are guarded, so `import dulus_bar.native` works
on any platform even if the native deps are missing.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import List, Optional

from .base import NativeBackend, NotchGeometry, WindowInfo

__all__ = [
    "NativeBackend",
    "NotchGeometry",
    "WindowInfo",
    "get_backend",
    "activate_window",
    "find_window_by_title",
    "jump_to_terminal",
    "open_terminal",
    "open_terminal_running",
    "notch_geometry",
    "configure_always_visible",
    "default_font_family",
]


@lru_cache(maxsize=1)
def get_backend() -> NativeBackend:
    """Return the singleton backend for the current OS (never raises)."""
    platform = sys.platform
    try:
        if platform.startswith("win"):
            from .windows import WindowsBackend

            return WindowsBackend()
        if platform == "darwin":
            from .macos import MacOSBackend

            return MacOSBackend()
        if platform.startswith("linux"):
            from .linux import LinuxBackend

            return LinuxBackend()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[native] backend init failed ({platform}): {exc}; using no-op backend")
    return NativeBackend()


# --- thin module-level convenience wrappers -------------------------------


def activate_window(handle: object) -> bool:
    return get_backend().activate_window(handle)


def find_window_by_title(title_substring: str) -> Optional[WindowInfo]:
    return get_backend().find_window_by_title(title_substring)


def jump_to_terminal(title_hint: str = "") -> bool:
    return get_backend().jump_to_terminal(title_hint)


def open_terminal() -> bool:
    return get_backend().open_terminal()


def open_terminal_running(command, title: str = "", cwd: Optional[str] = None) -> bool:
    return get_backend().open_terminal_running(command, title=title, cwd=cwd)


def notch_geometry() -> Optional[NotchGeometry]:
    return get_backend().notch_geometry()


def configure_always_visible(win: object) -> None:
    get_backend().configure_always_visible(win)


def default_font_family() -> str:
    return get_backend().default_font_family()


def list_windows() -> List[WindowInfo]:
    return get_backend().list_windows()
