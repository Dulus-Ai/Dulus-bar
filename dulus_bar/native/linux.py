"""Linux native backend (X11 / Wayland).

Linux has no single way to do any of this, so the backend is deliberately
defensive:

  * **Window placement** — X11 window managers apply their own placement policy
    to frameless windows, and Wayland gives a client no way to position itself
    at all (which is how the island ended up floating in the middle of the
    screen). We ask for XWayland when we're on Wayland, take the window out of
    the WM's placement policy on X11, and anchor to the *work area* so a top
    panel/dock never covers the island.
  * **Terminals** — every emulator spells "run this command" differently.
    ``gnome-terminal -e`` was removed upstream, so the old one-size-fits-all
    invocation started a process that instantly died with a usage error while
    we reported success: agents appeared to launch and nothing opened.
  * **Window activation** — jump-to-terminal uses wmctrl when present and falls
    back to xdotool (including converting wmctrl's hex window ids, which
    xdotool does not accept). Neither exists on Wayland, where activation is
    simply not available to clients.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from typing import List, Optional, Sequence, Tuple

from .base import NativeBackend, WindowInfo

# How long we wait to see whether a spawned terminal survived. Terminals that
# hand off to a server (gnome-terminal, ptyxis, konsole…) exit 0 immediately,
# which we treat as success; a non-zero exit means bad flags and we try the
# next candidate.
TERMINAL_PROBE_SECONDS = 0.45

_TRUTHY = ("1", "true", "True", "yes", "on")

# --- terminal command styles ---------------------------------------------
_DASHDASH = "dashdash"  # exe -- <argv>            (gnome-terminal, ptyxis, kgx)
_DASHE = "dashe"        # exe -e <argv>            (konsole, alacritty, xterm)
_DIRECT = "direct"      # exe <argv>               (kitty, foot)
_COMMAND = "command"    # exe --command "<string>" (xfce4-terminal, tilix)
_WEZTERM = "wezterm"    # exe start -- <argv>

# Ordered from "most likely to be the session's real terminal" downwards.
_TERMINAL_STYLES: Tuple[Tuple[str, str], ...] = (
    ("gnome-terminal", _DASHDASH),
    ("ptyxis", _DASHDASH),
    ("kgx", _DASHDASH),
    ("mate-terminal", _DASHDASH),
    ("konsole", _DASHE),
    ("xfce4-terminal", _COMMAND),
    ("tilix", _COMMAND),
    ("terminator", _COMMAND),
    ("lxterminal", _COMMAND),
    ("alacritty", _DASHE),
    ("kitty", _DIRECT),
    ("foot", _DIRECT),
    ("wezterm", _WEZTERM),
    ("ghostty", _DASHE),
    ("qterminal", _DASHE),
    ("deepin-terminal", _DASHE),
    ("urxvt", _DASHE),
    ("rxvt", _DASHE),
    ("st", _DASHE),
    ("xterm", _DASHE),
)

_STYLE_BY_EXE = dict(_TERMINAL_STYLES)

# Terminal the desktop session itself ships with, tried before the generic list.
_DESKTOP_TERMINALS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("kde", ("konsole",)),
    ("gnome", ("gnome-terminal", "kgx", "ptyxis")),
    ("xfce", ("xfce4-terminal",)),
    ("mate", ("mate-terminal",)),
    ("cinnamon", ("gnome-terminal",)),
    ("lxqt", ("qterminal",)),
    ("lxde", ("lxterminal",)),
    ("deepin", ("deepin-terminal",)),
    ("sway", ("foot", "alacritty")),
    ("hyprland", ("kitty", "foot", "alacritty")),
)


def session_type() -> str:
    """Best-effort session type: ``"wayland"``, ``"x11"`` or ``"unknown"``."""
    xdg = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    if xdg in ("wayland", "x11"):
        return xdg
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def is_wayland() -> bool:
    return session_type() == "wayland"


def has_x_server() -> bool:
    """True when an X server is reachable — a real one, or XWayland."""
    return bool(os.environ.get("DISPLAY"))


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "") in _TRUTHY


def _command_interpreter() -> str:
    """POSIX shell used to *run* the agent command (predictable ``-c``)."""
    for candidate in ("/bin/bash", "/usr/bin/bash", "/bin/sh", "/usr/bin/sh"):
        if os.path.exists(candidate):
            return candidate
    return "sh"


def _hold_shell() -> str:
    """Interactive shell left running so the terminal stays open afterwards."""
    shell = os.environ.get("SHELL") or ""
    if shell and os.path.exists(shell):
        return shell
    return _command_interpreter()


def _title_escape(title: str) -> str:
    """Strip anything that could break out of the OSC title sequence."""
    return "".join(ch for ch in (title or "") if ch.isprintable() and ch not in "\\'\"\x07")


def _terminal_argv(exe: str, style: str, interpreter: str, script: str) -> List[str]:
    """Build the argv that makes ``exe`` run ``script`` in a visible window."""
    inner = [interpreter, "-c", script]
    if style == _DASHDASH:
        return [exe, "--", *inner]
    if style == _DIRECT:
        return [exe, *inner]
    if style == _WEZTERM:
        return [exe, "start", "--", *inner]
    if style == _COMMAND:
        return [exe, "--command", " ".join(shlex.quote(part) for part in inner)]
    return [exe, "-e", *inner]  # _DASHE


def _style_for_exe(exe: str) -> str:
    """Command style for an executable, resolving Debian's alternatives link."""
    name = os.path.basename(exe)
    if name in _STYLE_BY_EXE:
        return _STYLE_BY_EXE[name]
    try:
        real = os.path.basename(os.path.realpath(shutil.which(exe) or exe))
    except Exception:
        real = name
    return _STYLE_BY_EXE.get(real, _DASHE)


def _preferred_terminals() -> List[str]:
    """Terminal executables to try first, based on $TERMINAL and the desktop."""
    names: List[str] = []
    env_term = (os.environ.get("TERMINAL") or "").strip()
    if env_term:
        names.append(env_term)
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()
    for key, terminals in _DESKTOP_TERMINALS:
        if key in desktop:
            names.extend(terminals)
    names.append("x-terminal-emulator")  # Debian/Ubuntu alternatives link
    return names


def terminal_candidates() -> List[Tuple[str, str]]:
    """Installed terminals as ``(path, style)``, best guess first."""
    found: List[Tuple[str, str]] = []
    seen: set = set()
    ordered = _preferred_terminals() + [name for name, _ in _TERMINAL_STYLES]
    for name in ordered:
        path = shutil.which(name)
        if not path:
            continue
        key = os.path.realpath(path)
        if key in seen:
            continue
        seen.add(key)
        found.append((path, _style_for_exe(name)))
    return found


class LinuxBackend(NativeBackend):
    name = "linux"

    def __init__(self) -> None:
        self._wmctrl = shutil.which("wmctrl")
        self._xdotool = shutil.which("xdotool")

    # --- window discovery / focus ---------------------------------------
    def list_windows(self, only_visible: bool = True) -> List[WindowInfo]:
        windows = self._list_windows_wmctrl()
        if windows:
            return windows
        return self._list_windows_xdotool()

    def _list_windows_wmctrl(self) -> List[WindowInfo]:
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

    def _list_windows_xdotool(self, limit: int = 80) -> List[WindowInfo]:
        """wmctrl-free fallback: ask xdotool for visible, named windows.

        Names and pids are fetched with two *chained* xdotool calls instead of
        two per window, so a busy desktop doesn't stall the UI thread.
        """
        if not self._xdotool:
            return []
        try:
            out = subprocess.run(
                [self._xdotool, "search", "--onlyvisible", "--name", "."],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
        except Exception:
            return []
        ids = [line.strip() for line in out.splitlines() if line.strip().isdigit()][:limit]
        if not ids:
            return []
        titles = self._xdotool_chain("getwindowname", ids)
        pids = self._xdotool_chain("getwindowpid", ids)
        if len(titles) != len(ids):
            return []
        results: List[WindowInfo] = []
        for index, win_id in enumerate(ids):
            try:
                pid = int(pids[index]) if index < len(pids) else 0
            except ValueError:
                pid = 0
            results.append(
                WindowInfo(
                    handle=win_id,
                    title=titles[index],
                    pid=pid,
                    process_name=_proc_name(pid),
                )
            )
        return results

    def _xdotool_chain(self, command: str, ids: Sequence[str]) -> List[str]:
        if not self._xdotool:
            return []
        argv: List[str] = [self._xdotool]
        for win_id in ids:
            argv.extend([command, win_id])
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=3).stdout
        except Exception:
            return []
        return out.splitlines()

    def activate_window(self, handle: object) -> bool:
        win_id = str(handle).strip()
        if not win_id:
            return False
        if self._wmctrl:
            try:
                r = subprocess.run([self._wmctrl, "-ia", win_id], capture_output=True, timeout=2)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        if self._xdotool:
            # xdotool wants a decimal id; wmctrl hands out hex ones.
            xid = win_id
            if xid.lower().startswith("0x"):
                try:
                    xid = str(int(xid, 16))
                except ValueError:
                    return False
            for args in (["windowactivate", "--sync", xid], ["windowraise", xid]):
                try:
                    r = subprocess.run([self._xdotool, *args], capture_output=True, timeout=2)
                    if r.returncode == 0:
                        return True
                except Exception:
                    continue
        return False

    # --- terminals -------------------------------------------------------
    def open_terminal(self) -> bool:
        for exe, _style in terminal_candidates():
            if self._spawn([exe], None):
                return True
        return False

    def open_terminal_running(self, command, title="", cwd=None) -> bool:
        inner = " ".join(shlex.quote(str(c)) for c in command)
        interpreter = _command_interpreter()
        script = inner
        clean_title = _title_escape(title)
        if clean_title:
            # Set the window title from inside the shell: the flag for it is
            # different (or gone) in every emulator, and the title is what
            # click-to-jump matches on later.
            script = f"printf '\\033]0;{clean_title}\\007'; {script}"
        # keep the shell open after the agent exits so the user sees output
        script = f"{script}; exec {shlex.quote(_hold_shell())}"
        for exe, style in terminal_candidates():
            if self._spawn(_terminal_argv(exe, style, interpreter, script), cwd):
                return True
        return False

    @staticmethod
    def _spawn(argv: Sequence[str], cwd: Optional[str]) -> bool:
        """Start a terminal and confirm it didn't die on its own arguments."""
        try:
            proc = subprocess.Popen(
                list(argv),
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            return False
        try:
            code = proc.wait(timeout=TERMINAL_PROBE_SECONDS)
        except subprocess.TimeoutExpired:
            return True  # still running: the window is up
        except Exception:
            return True
        # Exited already: 0 means it handed off to a terminal server, anything
        # else means it rejected our arguments — try the next emulator.
        return code == 0

    # --- overlay placement ----------------------------------------------
    def prepare_environment(self) -> None:
        if os.environ.get("QT_QPA_PLATFORM") or not is_wayland():
            return
        if _env_flag("DULUS_BAR_KEEP_WAYLAND"):
            return
        if not has_x_server():
            print(
                "[native] Wayland session without XWayland: the compositor decides where "
                "the island goes. Start XWayland or use an X11 session to pin it to the top."
            )
            return
        # Wayland clients cannot position themselves, so the island lands
        # wherever the compositor likes (usually screen centre). XWayland can.
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        print(
            "[native] Wayland detected: running on XWayland so the island can pin itself "
            "to the top edge (set DULUS_BAR_KEEP_WAYLAND=1 to keep native Wayland)."
        )

    def overlay_use_available_geometry(self) -> bool:
        # Respect panels/docks: a 6px peek flush with the physical top edge is
        # unreachable under a GNOME top bar.
        return not _env_flag("DULUS_BAR_FULL_SCREEN_EDGE")

    def overlay_extra_window_flags(self) -> Tuple[str, ...]:
        if not has_x_server() or _env_flag("DULUS_BAR_X11_WM"):
            return ()
        # Opt out of the window manager's placement policy: this is what stops
        # GNOME/KDE/i3 from re-centring the island the moment it is mapped.
        return ("X11BypassWindowManagerHint",)

    def overlay_settle_delays_ms(self) -> Tuple[int, ...]:
        # Some WMs re-place a window several hundred ms after mapping, so keep
        # restating our position while things settle.
        return (0, 40, 120, 350, 800, 1500)

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
