"""``dulusbar doctor``: one screen that says what's wrong and how to fix it."""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import sys

from . import __version__
from . import protocol as P

OK, WARN, BAD, INFO = "✓", "!", "✗", "·"


def _line(mark: str, label: str, detail: str = "") -> None:
    print(f"  {mark} {label:<16} {detail}".rstrip())


def _deps() -> int:
    problems = 0
    for module, purpose in (("PySide6", "the island UI"), ("websockets", "the hub"),
                            ("psutil", "tracking agent processes")):
        try:
            found = importlib.import_module(module)
            _line(OK, module, str(getattr(found, "__version__", "")))
        except Exception:
            _line(BAD, module, f"missing — needed for {purpose}: pip install {module}")
            problems += 1
    return problems


def _island() -> None:
    from .client import probe

    url = P.server_url()
    info = probe(url)
    if info:
        _line(OK, "island", f"running at {url} ({info.get('server', '?')})")
    elif info == {}:
        _line(WARN, "island", f"an older Dulus Bar is running at {url}; quit it and start this one")
    else:
        _line(INFO, "island", "not running — start it with: dulusbar")


def _integrations() -> int:
    from .integrations import claude, codex

    problems = 0
    for name, module in (("Claude Code", claude), ("Codex", codex)):
        st = module.status()
        if st.get("error"):
            _line(BAD, name, st["error"])
            problems += 1
        elif st.get("installed") and st.get("current"):
            _line(OK, name, f"connected ({st['path']})")
        elif st.get("installed"):
            _line(WARN, name, "connected to another install — run: dulusbar hooks install")
        elif st.get("partial"):
            _line(WARN, name, "partly connected — run: dulusbar hooks install")
        elif st.get("other_notify"):
            _line(WARN, name, f"notify is used by another program ({st['path']})")
        else:
            _line(INFO, name, f"not connected — run: dulusbar hooks install {name.split()[0].lower()}")
    return problems


def _linux() -> int:
    from .native import linux as lx

    session = lx.session_type()
    if session == "unknown":
        _line(WARN, "display", "no display found (DISPLAY / WAYLAND_DISPLAY are unset)")
    elif session == "wayland" and not lx.has_x_server():
        _line(WARN, "display", "Wayland without XWayland: the compositor decides where the island goes")
    else:
        _line(OK, "display", session + (" (via XWayland)" if session == "wayland" else ""))
    terminals = [os.path.basename(path) for path, _ in lx.terminal_candidates()]
    if terminals:
        _line(OK, "terminals", ", ".join(terminals[:4]))
    else:
        _line(BAD, "terminals", "none found — install one or set $TERMINAL to launch agents")
        return 1
    if shutil.which("wmctrl") or shutil.which("xdotool"):
        _line(OK, "focus", "wmctrl/xdotool present")
    else:
        _line(INFO, "focus", "install wmctrl or xdotool for click-to-focus on X11")
    return 0


def _macos() -> int:
    from . import macos_native

    try:
        importlib.import_module("AppKit")
        _line(OK, "pyobjc", "present")
    except Exception:
        _line(INFO, "pyobjc", "missing — the Qt fallback won't hug the notch")
    binary = macos_native.native_binary()
    if macos_native.native_available():
        stale = macos_native.native_stale()
        _line(WARN if stale else OK, "native island",
              "built, but older than its sources (rebuilt on next start)" if stale else str(binary))
    elif shutil.which("swift"):
        _line(INFO, "native island", "not built yet (built automatically on first start)")
    else:
        _line(INFO, "native island", "no Swift toolchain — using the Qt island (xcode-select --install)")
    return 0


def _windows() -> int:
    try:
        importlib.import_module("win32gui")
        _line(OK, "pywin32", "present")
        return 0
    except Exception:
        _line(BAD, "pywin32", "missing — needed for click-to-focus: pip install pywin32")
        return 1


def run_doctor() -> int:
    from . import autostart
    from .integrations import PACKAGE_DIR, is_frozen

    print(f"Dulus Bar {__version__} · Python {platform.python_version()} · "
          f"{platform.system()} {platform.release()}")
    _line(INFO, "install", "frozen binary" if is_frozen() else str(PACKAGE_DIR))
    _line(INFO, "settings", str(P.app_dir()))
    problems = _deps()
    _island()
    problems += _integrations()
    enabled = autostart.is_enabled()
    _line(OK if enabled else INFO, "start at login", "on" if enabled else "off (tray menu → Start at login)")
    try:
        if sys.platform.startswith("linux"):
            problems += _linux()
        elif sys.platform == "darwin":
            problems += _macos()
        elif sys.platform == "win32":
            problems += _windows()
    except Exception as exc:  # a diagnostic must never crash
        _line(WARN, "platform", f"check failed: {exc}")
    print()
    print("All good." if not problems else f"{problems} problem(s) found.")
    return 1 if problems else 0
