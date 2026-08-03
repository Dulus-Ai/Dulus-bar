"""macOS native backend: camera-notch geometry + always-visible overlay.

Depends on pyobjc (``pyobjc-framework-Cocoa`` / ``-Quartz``). Every AppKit call
is guarded so that on a machine without pyobjc the backend simply degrades to
osascript-based activation and a plain top-most window (no notch hugging).
"""

from __future__ import annotations

import subprocess
from typing import List, Optional

from .base import NativeBackend, NotchGeometry, WindowInfo

try:  # pragma: no cover - platform specific
    import AppKit  # type: ignore
    import objc  # type: ignore

    _HAVE_APPKIT = True
except Exception:  # pragma: no cover
    _HAVE_APPKIT = False

try:  # pragma: no cover - platform specific
    import Quartz  # type: ignore

    _HAVE_QUARTZ = True
except Exception:  # pragma: no cover
    _HAVE_QUARTZ = False


# NSWindow level / collection-behavior constants (avoid importing symbols that
# may be missing on older pyobjc builds).
_NS_MAIN_MENU_LEVEL = 24
_CB_CAN_JOIN_ALL_SPACES = 1 << 0  # 1
_CB_STATIONARY = 1 << 4  # 16
_CB_FULLSCREEN_AUX = 1 << 8  # 256


class MacOSBackend(NativeBackend):
    name = "macos"

    # --- notch ----------------------------------------------------------
    def notch_geometry(self) -> Optional[NotchGeometry]:
        if not _HAVE_APPKIT:
            return None
        try:
            screen = AppKit.NSScreen.mainScreen()
            if screen is None:
                return None
            frame = screen.frame()
            screen_w = int(round(frame.size.width))
            screen_h = int(round(frame.size.height))

            # safeAreaInsets.top > 0 is the reliable "has notch" signal (macOS 12+).
            top_inset = 0.0
            if screen.respondsToSelector_("safeAreaInsets"):
                try:
                    top_inset = float(screen.safeAreaInsets().top)
                except Exception:
                    top_inset = 0.0

            if top_inset <= 0:
                return None  # no notch on this display

            # Width of the notch = screen width minus the two menu-bar wings.
            notch_w = 0
            if screen.respondsToSelector_("auxiliaryTopLeftArea") and screen.respondsToSelector_(
                "auxiliaryTopRightArea"
            ):
                try:
                    left = screen.auxiliaryTopLeftArea()
                    right = screen.auxiliaryTopRightArea()
                    notch_w = int(round(screen_w - left.size.width - right.size.width))
                except Exception:
                    notch_w = 0
            if notch_w <= 0:
                notch_w = 210  # sensible default (~14"/16" MBP notch width in pt)

            notch_h = int(round(top_inset))
            x = (screen_w - notch_w) // 2
            return NotchGeometry(
                x=x,
                y=0,
                width=notch_w,
                height=notch_h,
                screen_width=screen_w,
                screen_height=screen_h,
            )
        except Exception:
            return None

    # --- always visible -------------------------------------------------
    def configure_always_visible(self, win: object) -> None:
        if not _HAVE_APPKIT or win is None:
            return
        try:
            win_id = int(win.winId())  # NSView* pointer on macOS
            view = objc.objc_object(c_void_p=win_id)
            ns_window = view.window()
            if ns_window is None:
                return
            # Sit just above the menu bar so the island reads as part of the notch.
            ns_window.setLevel_(_NS_MAIN_MENU_LEVEL + 1)
            ns_window.setCollectionBehavior_(
                _CB_CAN_JOIN_ALL_SPACES | _CB_STATIONARY | _CB_FULLSCREEN_AUX
            )
            ns_window.setHidesOnDeactivate_(False)
            try:
                ns_window.setMovableByWindowBackground_(False)
            except Exception:
                pass
        except Exception:
            # Best effort — worst case it behaves like a normal top-most window.
            return

    # --- window discovery / focus (Quartz + osascript) ------------------
    def list_windows(self, only_visible: bool = True) -> List[WindowInfo]:
        if not _HAVE_QUARTZ:
            return []
        try:
            opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
            infos = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
        except Exception:
            return []
        out: List[WindowInfo] = []
        for info in infos or []:
            try:
                owner = str(info.get("kCGWindowOwnerName", "") or "")
                title = str(info.get("kCGWindowName", "") or "")
                pid = int(info.get("kCGWindowOwnerPID", 0) or 0)
            except Exception:
                continue
            # handle carries the pid so activate_window can raise the whole app.
            out.append(WindowInfo(handle=pid, title=title or owner, pid=pid, process_name=owner.lower()))
        return out

    def activate_window(self, handle: object) -> bool:
        pid = handle if isinstance(handle, int) else 0
        if _HAVE_APPKIT and pid:
            try:
                app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
                if app is not None:
                    # NSApplicationActivateIgnoringOtherApps = 1 << 1
                    return bool(app.activateWithOptions_(1 << 1))
            except Exception:
                pass
        return False

    def jump_to_terminal(self, title_hint: str = "") -> bool:
        # Try the precise window first (needs Screen-Recording perm for titles).
        if super().jump_to_terminal(title_hint):
            return True
        # Fallback: activate a common terminal / editor by app name.
        for app in ("iTerm", "Terminal", "Ghostty", "Warp", "Visual Studio Code", "Cursor"):
            if self._osascript_activate(app):
                return True
        return False

    def open_terminal(self) -> bool:
        try:
            subprocess.Popen(["open", "-a", "Terminal"])
            return True
        except Exception:
            return False

    def open_terminal_running(self, command, title="", cwd=None) -> bool:
        import shlex

        parts = []
        if cwd:
            parts.append(f"cd {shlex.quote(str(cwd))}")
        parts.append(" ".join(shlex.quote(str(c)) for c in command))
        shell_cmd = " && ".join(parts)
        # Prefer iTerm if present, else Terminal.
        script_iterm = (
            'tell application "iTerm"\n'
            ' create window with default profile\n'
            f' tell current session of current window to write text "{shell_cmd}"\n'
            ' activate\n'
            'end tell'
        )
        script_term = f'tell application "Terminal" to do script "{shell_cmd}"\ntell application "Terminal" to activate'
        for script in (script_term, script_iterm):
            try:
                r = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
                if r.returncode == 0:
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _osascript_activate(app_name: str) -> bool:
        try:
            r = subprocess.run(
                ["osascript", "-e", f'tell application "{app_name}" to activate'],
                capture_output=True,
                timeout=2,
            )
            return r.returncode == 0
        except Exception:
            return False

    def default_font_family(self) -> str:
        return "SF Pro Text"
