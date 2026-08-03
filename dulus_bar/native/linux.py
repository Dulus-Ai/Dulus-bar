"""Linux (X11) native backend using wmctrl / xdotool when available.

Wayland exposes no portable window-activation API, so on Wayland the overlay
still shows and stays on top (handled by Qt) but jump-to-terminal degrades to a
best-effort no-op. X11 with wmctrl or xdotool installed gets full behavior.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional

from .base import NativeBackend, WindowInfo


class LinuxBackend(NativeBackend):
    name = "linux"

    def __init__(self) -> None:
        self._wmctrl = shutil.which("wmctrl")
        self._xdotool = shutil.which("xdotool")

    def list_windows(self, only_visible: bool = True) -> List[WindowInfo]:
        if not self._wmctrl:
            return []
        try:
            out = subprocess.run(
                [self._wmctrl, "-lp"], capture_output=True, text=True, timeout=2
            ).stdout
        except Exception:
            return []
        results: List[WindowInfo] = []
        for line in out.splitlines():
            # id  desktop  pid  host  title...
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            win_id, _desktop, pid_s, _host, title = parts
            try:
                pid = int(pid_s)
            except ValueError:
                pid = 0
            results.append(WindowInfo(handle=win_id, title=title, pid=pid, process_name=_proc_name(pid)))
        return results

    def activate_window(self, handle: object) -> bool:
        win_id = str(handle)
        if self._wmctrl:
            try:
                r = subprocess.run([self._wmctrl, "-ia", win_id], capture_output=True, timeout=2)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        if self._xdotool:
            try:
                r = subprocess.run([self._xdotool, "windowactivate", win_id], capture_output=True, timeout=2)
                return r.returncode == 0
            except Exception:
                return False
        return False

    def open_terminal(self) -> bool:
        for exe in ("x-terminal-emulator", "gnome-terminal", "konsole", "alacritty", "xterm"):
            path = shutil.which(exe)
            if path:
                try:
                    subprocess.Popen([path])
                    return True
                except Exception:
                    continue
        return False

    def open_terminal_running(self, command, title="", cwd=None) -> bool:
        import shlex

        inner = " ".join(shlex.quote(str(c)) for c in command)
        # keep the shell open after the agent exits so the user sees output
        bash_cmd = f"{inner}; exec bash"
        for exe in ("x-terminal-emulator", "gnome-terminal", "konsole", "alacritty", "xterm", "kitty"):
            path = shutil.which(exe)
            if not path:
                continue
            try:
                subprocess.Popen([path, "-e", "bash", "-c", bash_cmd], cwd=cwd)
                return True
            except Exception:
                continue
        return False

    def default_font_family(self) -> str:
        return "Noto Sans"


def _proc_name(pid: int) -> str:
    if not pid:
        return ""
    try:
        with open(f"/proc/{pid}/comm", "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip().lower()
    except Exception:
        return ""
