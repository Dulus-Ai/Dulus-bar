"""Start Dulus Bar at login: XDG autostart, a LaunchAgent, or the Run key."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

LABEL = "ai.dulus.bar"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "Dulus Bar"


def _argv() -> List[str]:
    from .integrations import self_argv

    argv = self_argv("run")
    if os.name == "nt" and argv[0].lower().endswith("python.exe"):
        windowed = argv[0][: -len("python.exe")] + "pythonw.exe"
        if os.path.exists(windowed):
            argv[0] = windowed  # no console flash at login
    return argv


def _desktop_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "autostart" / "dulusbar.desktop"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _desktop_quote(arg: str) -> str:
    """Quote one Exec= argument per the Desktop Entry spec."""
    if arg and not any(ch in arg for ch in " \t\n\"'\\><~|&;$*?#()`"):
        return arg
    escaped = "".join("\\" + ch if ch in '"`$\\' else ch for ch in arg)
    return f'"{escaped}"'


def is_enabled() -> bool:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, _RUN_VALUE)
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return _plist_path().exists()
    return _desktop_path().exists()


def enable() -> str:
    argv = _argv()
    if sys.platform == "win32":
        import subprocess
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(argv))
        return rf"HKCU\{_RUN_KEY}\{_RUN_VALUE}"
    if sys.platform == "darwin":
        import plistlib

        path = _plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        plist = {
            "Label": LABEL,
            "ProgramArguments": argv,
            "RunAtLoad": True,
            "ProcessType": "Interactive",
            # launchd's PATH is bare; keep ours so agents can be found and launched.
            "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        }
        with path.open("wb") as fh:
            plistlib.dump(plist, fh)
        return str(path)
    path = _desktop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Dulus Bar\n"
        "Comment=Floating status island for your AI coding agents\n"
        f"Exec={' '.join(_desktop_quote(a) for a in argv)}\n"
        "Icon=dulusbar\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
    return str(path)


def disable() -> None:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, _RUN_VALUE)
        except OSError:
            pass
        return
    path = _plist_path() if sys.platform == "darwin" else _desktop_path()
    try:
        path.unlink()
    except OSError:
        pass
