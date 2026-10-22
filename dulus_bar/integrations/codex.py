"""Codex CLI: turn-complete notifications through its ``notify`` program.

Codex runs ``notify`` with one JSON argument after every agent turn:

    {"type": "agent-turn-complete", "thread-id": "...", "turn-id": "...",
     "cwd": "...", "input-messages": [...], "last-assistant-message": "..."}

``dulusbar hooks install codex`` points ``notify`` at ``dulusbar hook codex``,
so the island lights up with Codex's last message the moment it hands the
turn back to you. For live status and Allow / Deny, wrap it instead:
``dulusbar wrap Codex -- codex``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import protocol as P
from ..client import send_event
from ..text import first_line
from . import ConfigError, self_argv, write_text

AGENT = "Codex"
MARKER = "# dulus-bar"
_NOTIFY = re.compile(r"^\s*notify\s*=")
_TABLE = re.compile(r"^\s*\[")


def handle_notify(raw: str) -> int:
    """Entry point Codex runs. Always exits 0."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return 0
    if not isinstance(data, dict) or data.get("type") != "agent-turn-complete":
        return 0
    thread = str(data.get("thread-id") or data.get("turn-id") or "codex")
    text = first_line(data.get("last-assistant-message"))
    payload: Dict[str, Any] = {"text": text or "Your turn", "pid_host": P.host_id()}
    if data.get("cwd"):
        payload["cwd"] = str(data["cwd"])
    send_event(AGENT, P.COMPLETED, thread, payload)
    return 0


def config_path() -> Path:
    base = os.environ.get("CODEX_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".codex") / "config.toml"


def notify_line() -> str:
    argv = self_argv("hook", "codex")
    # JSON string escapes are valid TOML basic-string escapes.
    return "notify = [" + ", ".join(json.dumps(a) for a in argv) + f"]  {MARKER}"


def _top_level(lines: List[str]) -> int:
    """Index of the first table header: top-level keys must come before it.

    A ``[`` inside a multi-line string is not a table header.
    """
    in_string = ""
    for index, line in enumerate(lines):
        for quote in ('"""', "'''"):
            if line.count(quote) % 2 and in_string in ("", quote):
                in_string = "" if in_string else quote
        if not in_string and _TABLE.match(line):
            return index
    return len(lines)


def _find_notify(lines: List[str]) -> Optional[Tuple[int, bool]]:
    for index in range(_top_level(lines)):
        if _NOTIFY.match(lines[index]):
            return index, MARKER in lines[index] or "hook codex" in lines[index]
    return None


def _read(path: Path) -> Tuple[str, List[str], str]:
    """(BOM, lines, newline) — all three are written back as they were."""
    try:
        text = ""
        if path.exists():
            with path.open(encoding="utf-8", newline="") as fh:  # keep \r\n as-is
                text = fh.read()
    except OSError as exc:
        raise ConfigError(f"can't read {path}: {exc}") from exc
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    text = text[len(bom):]
    return bom, text.splitlines(), "\r\n" if "\r\n" in text else "\n"


def _parsed(text: str, path: Path) -> Optional[Dict[str, Any]]:
    """The file through a real TOML parser (Python 3.11+); None without one."""
    try:
        import tomllib
    except ImportError:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML ({exc}); fix it first, nothing was changed") from exc


def install(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or config_path()
    bom, lines, newline = _read(path)
    current = _parsed(newline.join(lines), path)
    found = _find_notify(lines)
    if (found is not None and not found[1]) or (found is None and current and "notify" in current):
        raise ConfigError(
            f"{path} already sets notify = … for another program; Dulus Bar won't overwrite it. "
            f"Add this instead, or chain the two:\n  {notify_line()}"
        )
    if found is not None:
        lines[found[0]] = notify_line()
    else:
        lines.insert(0, notify_line())
    text = newline.join(lines) + newline
    after = _parsed(text, path)
    if after is not None and after.get("notify") != self_argv("hook", "codex"):
        raise ConfigError(f"couldn't add notify to {path} safely; nothing was changed")
    write_text(path, bom + text)
    return {"path": str(path), "line": notify_line()}


def uninstall(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or config_path()
    bom, lines, newline = _read(path)
    found = _find_notify(lines)
    if found is None or not found[1]:
        return {"path": str(path), "removed": 0}
    del lines[found[0]]
    write_text(path, bom + newline.join(lines) + (newline if lines else ""))
    return {"path": str(path), "removed": 1}


def status(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or config_path()
    try:
        _, lines, _ = _read(path)
    except ConfigError as exc:
        return {"path": str(path), "installed": False, "error": str(exc)}
    found = _find_notify(lines)
    ours = found is not None and found[1]
    return {
        "path": str(path),
        "installed": ours,
        "other_notify": found is not None and not found[1],
        "current": ours and lines[found[0]].strip() == notify_line(),
    }
