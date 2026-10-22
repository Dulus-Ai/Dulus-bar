"""Claude Code: live status on the island, and Allow / Deny from it.

``dulusbar hooks install claude`` adds hooks to ``~/.claude/settings.json``.
Each one is a short-lived process that reads the hook JSON on stdin, tells
the hub, and exits:

* the status hooks (SessionStart, UserPromptSubmit, Pre/PostToolUse,
  Notification, Stop) run ``async``, so they add no latency to Claude Code;
* the PermissionRequest hook waits for your answer. Claude Code shows its own
  dialog at the same time and takes whichever answer comes first — approve on
  the island *or* in the terminal;
* it never breaks Claude Code: with no island running, or on any error at
  all, the hook exits 0 without output and Claude carries on as usual.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple

from .. import protocol as P
from ..client import BarClient, BarError, BarUnavailable
from ..text import first_line, one_line
from ..toolcalls import describe_suggestions, summarize
from . import ConfigError, load_json, self_argv, shell_command, windows_hook_shell, write_json

AGENT = "Claude Code"
DENY_MESSAGE = (
    "The user denied this from Dulus Bar. Don't retry it; ask the user how they'd like to proceed."
)

# Hook event -> options for our entry ("matcher" goes on the matcher group).
HOOK_EVENTS: Dict[str, Dict[str, Any]] = {
    "SessionStart": {"async": True, "timeout": 10},
    "UserPromptSubmit": {"async": True, "timeout": 10},
    "PreToolUse": {"matcher": "*", "async": True, "timeout": 10},
    "PostToolUse": {"matcher": "*", "async": True, "timeout": 10},
    # Waits for the island; Claude's own dialog runs alongside it.
    "PermissionRequest": {"matcher": "*", "timeout": 86400},
    "Notification": {"async": True, "timeout": 10},
    "Stop": {"async": True, "timeout": 10},
    "SessionEnd": {"timeout": 5},
}

_SOURCES = {"resume": "Resumed", "clear": "Cleared", "compact": "Compacted", "fork": "Forked"}
_SHELLS = frozenset({
    "sh", "bash", "zsh", "dash", "fish", "ksh", "mksh", "tcsh", "csh", "env",
    "pwsh", "powershell", "cmd", "sh.exe", "bash.exe", "pwsh.exe", "powershell.exe", "cmd.exe",
})
_MODEL_RE = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?(\[1m\])?$", re.I)


# --- small helpers ---------------------------------------------------------------


def pretty_model(model: str) -> str:
    """``claude-opus-4-5-20251101`` -> ``opus 4.5``; anything else unchanged."""
    match = _MODEL_RE.match((model or "").strip())
    if not match:
        return (model or "").strip()
    family, major, minor, wide = match.groups()
    version = f"{major}.{minor}" if minor else major
    return f"{family.lower()} {version}" + (" 1M" if wide else "")


def format_tokens(count: int) -> str:
    if count < 1000:
        return str(count)
    if count < 10_000:
        return f"{count / 1000:.1f}k"
    if count < 1_000_000:
        return f"{round(count / 1000)}k"
    return f"{count / 1_000_000:.1f}M"


def transcript_meta(path: Any) -> Dict[str, str]:
    """Model + context size from the newest main-thread turn in a transcript."""
    if not path:
        return {}
    try:
        with open(str(path), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 262_144))
            tail = fh.read()
    except OSError:
        return {}
    for raw in reversed(tail.splitlines()):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue  # includes the partial first line of the tail
        if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        usage = message.get("usage")
        model = str(message.get("model") or "")
        if not isinstance(usage, dict) or model.startswith("<"):  # "<synthetic>"
            continue
        tokens = sum(
            int(usage.get(k) or 0)
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        )
        meta = {"model": pretty_model(model)} if model else {}
        if tokens:
            meta["ctx"] = format_tokens(tokens)
        return meta
    return {}


def _proc(pid: int) -> Tuple[str, int]:
    """(name, parent pid) of a process, best effort."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            raw = fh.read().decode("utf-8", "replace")
        return raw[raw.index("(") + 1: raw.rindex(")")], int(raw[raw.rindex(")") + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        import psutil

        proc = psutil.Process(pid)
        return proc.name(), proc.ppid()
    except Exception:
        return "", 0


def agent_pid() -> int:
    """PID of the Claude Code process that ran this hook.

    Skips shells *and* launchers of our own executable: a onefile bootloader
    (PyInstaller / Nuitka) or the Windows venv ``python.exe`` redirector is our
    parent, and it dies with the hook — the hub would drop the session.
    """
    own = os.path.basename(_proc(os.getpid())[0]).lower()
    pid = os.getppid()
    for _ in range(6):
        name, parent = _proc(pid)
        base = os.path.basename(name).lower()
        if (base not in _SHELLS and base != own) or parent <= 1:
            return pid
        pid = parent
    return pid


# --- the hook ---------------------------------------------------------------------


def _event_for(data: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    name = data.get("hook_event_name")
    cwd = str(data.get("cwd") or "")
    if name == "SessionStart":
        payload: Dict[str, Any] = {"text": _SOURCES.get(str(data.get("source")), "Started")}
        if data.get("model"):
            payload["model"] = pretty_model(str(data["model"]))
        return P.SESSION_STARTED, payload
    if name == "UserPromptSubmit":
        return P.PROMPT, {"text": one_line(data.get("prompt"), 160) or "Working…"}
    if name in ("PreToolUse", "PostToolUse"):
        title, args, _ = summarize(data.get("tool_name"), data.get("tool_input"), cwd)
        if name == "PreToolUse":
            return P.TOOL_STARTED, {"tool": title, "args": args}
        return P.TOOL_FINISHED, {"tool": title, "args": args, **transcript_meta(data.get("transcript_path"))}
    if name == "Notification":
        text = one_line(data.get("message"), 160)
        if data.get("notification_type") == "idle_prompt":
            return P.IDLE, {"text": text or "Waiting for you"}
        return (P.MESSAGE, {"text": text}) if text else None
    if name == "Stop":
        payload = {"text": first_line(data.get("last_assistant_message")) or "Your turn"}
        payload.update(transcript_meta(data.get("transcript_path")))
        return P.COMPLETED, payload
    if name == "SessionEnd":
        return P.SESSION_ENDED, {"reason": str(data.get("reason") or "")}
    return None


def _permission(data: Dict[str, Any], session_id: str, base: Dict[str, Any], out: TextIO) -> int:
    title, args, detail = summarize(data.get("tool_name"), data.get("tool_input"), base["cwd"])
    suggestions = data.get("permission_suggestions")
    suggestions = suggestions if isinstance(suggestions, list) else []
    try:
        bar = BarClient(AGENT, session_id, timeout=1.0, client="claude-code-hook").connect()
    except (BarUnavailable, BarError, OSError):
        return 0  # no island: Claude's own dialog handles it
    try:
        decision = bar.request_permission(
            title, args, detail=detail, can_always=bool(suggestions),
            always_label=describe_suggestions(suggestions), extra=base,
        )
    finally:
        bar.close()
    if decision is None:
        return 0  # answered in the terminal, or the island went away
    if decision.approved:
        verdict: Dict[str, Any] = {"behavior": "allow"}
        if decision.always and suggestions:
            verdict["updatedPermissions"] = suggestions
    else:
        verdict = {"behavior": "deny", "message": decision.message or DENY_MESSAGE}
    out.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": verdict}}))
    out.flush()
    return 0


def handle(data: Dict[str, Any], out: Optional[TextIO] = None) -> int:
    session_id = str(data.get("session_id") or "")
    if not session_id:
        return 0
    base = {"cwd": str(data.get("cwd") or ""), "pid": agent_pid(), "pid_host": P.host_id()}
    if data.get("hook_event_name") == "PermissionRequest":
        return _permission(data, session_id, base, out or sys.stdout)
    mapped = _event_for(data)
    if mapped is None:
        return 0
    kind, payload = mapped
    try:
        with BarClient(AGENT, session_id, timeout=1.0, client="claude-code-hook") as bar:
            bar.event(kind, {**base, **payload})
    except (BarUnavailable, BarError, OSError):
        pass
    return 0


def _debug_log(exc: BaseException) -> None:
    if os.environ.get("DULUS_BAR_DEBUG") not in ("1", "true", "yes"):
        return
    try:
        path = P.app_dir() / "hooks.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} claude hook: {exc!r}\n")
    except OSError:
        pass


def hook_main(stdin: Any = None, out: Optional[TextIO] = None) -> int:
    """Entry point Claude Code runs. Always exits 0: never block the agent."""
    try:
        raw = (stdin or sys.stdin.buffer).read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        data = json.loads(raw)
        if isinstance(data, dict):
            return handle(data, out)
    except Exception as exc:
        _debug_log(exc)
    return 0


# --- installer ----------------------------------------------------------------------


def settings_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base).expanduser() if base else Path.home() / ".claude") / "settings.json"


def _shell() -> str:
    return windows_hook_shell() if os.name == "nt" else "bash"


def hook_command(shell: Optional[str] = None) -> str:
    return shell_command(self_argv("hook", "claude"), shell or _shell())


_OURS = re.compile(
    r"(?:dulus_bar[\\/]+main\.py|dulus[ _-]?bar(?:\.exe)?)\s+hook\s+claude\s*$", re.I
)


def is_ours(hook: Any) -> bool:
    """Our hook command: ``<python> …/dulus_bar/main.py hook claude`` or a
    frozen ``DulusBar[.exe] hook claude`` — and nothing else."""
    if not isinstance(hook, dict):
        return False
    plain = str(hook.get("command") or "").replace("'", "").replace('"', "")
    return bool(_OURS.search(plain.strip()))


def _groups(hooks: Dict[str, Any], event: str) -> List[Any]:
    groups = hooks.get(event)
    return groups if isinstance(groups, list) else []


def _strip(hooks: Dict[str, Any]) -> int:
    """Remove our entries (and only ours); return how many were removed."""
    removed = 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept_groups.append(group)
                continue
            kept = [h for h in entries if not is_ours(h)]
            removed += len(entries) - len(kept)
            if kept or not entries:
                kept_groups.append({**group, "hooks": kept} if len(kept) != len(entries) else group)
        if kept_groups:
            hooks[event] = kept_groups
        elif groups:
            del hooks[event]
    return removed


def install(path: Optional[Path] = None, *, shell: Optional[str] = None) -> Dict[str, Any]:
    """Add (or refresh) our hooks, leaving every other hook untouched."""
    path = path or settings_path()
    settings = load_json(path)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ConfigError(f"'hooks' in {path} is not an object; nothing was changed")
    for event in HOOK_EVENTS:
        if event in hooks and not isinstance(hooks[event], list):
            raise ConfigError(f"'hooks.{event}' in {path} is not a list; nothing was changed")
    _strip(hooks)
    shell = shell or _shell()
    command = hook_command(shell)
    for event, options in HOOK_EVENTS.items():
        entry: Dict[str, Any] = {"type": "command", "command": command}
        entry.update({k: v for k, v in options.items() if k != "matcher"})
        if shell == "powershell":
            entry["shell"] = "powershell"
        group: Dict[str, Any] = {"hooks": [entry]}
        if "matcher" in options:
            group = {"matcher": options["matcher"], **group}
        hooks.setdefault(event, []).append(group)
    write_json(path, settings)
    return {"path": str(path), "command": command, "events": list(HOOK_EVENTS)}


def uninstall(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or settings_path()
    settings = load_json(path)
    hooks = settings.get("hooks")
    removed = _strip(hooks) if isinstance(hooks, dict) else 0
    if removed:
        if not hooks:
            del settings["hooks"]
        write_json(path, settings)
    return {"path": str(path), "removed": removed}


def status(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or settings_path()
    try:
        settings = load_json(path)
    except ConfigError as exc:
        return {"path": str(path), "installed": False, "partial": False, "error": str(exc)}
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    found: Dict[str, List[str]] = {}
    for event in hooks:
        for group in _groups(hooks, event):
            entries = group.get("hooks") if isinstance(group, dict) else None
            for hook in entries if isinstance(entries, list) else []:
                if is_ours(hook):
                    found.setdefault(event, []).append(str(hook.get("command")))
    commands = sorted({c for cs in found.values() for c in cs})
    missing = [e for e in HOOK_EVENTS if e not in found]
    return {
        "path": str(path),
        "installed": bool(found) and not missing,
        "partial": bool(found) and bool(missing),
        "missing": missing,
        "current": commands == [hook_command()],
        "commands": commands,
    }
