"""Turn an agent's tool call into something you can approve at a glance."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple

from .text import one_line

_DETAIL_LINES = 12


def _rel(path: str, cwd: str) -> str:
    root = (cwd or "").rstrip("/\\")
    if root and path.startswith(root) and path[len(root):len(root) + 1] in ("/", "\\"):
        return path[len(root) + 1:]
    return path


def _head(text: str, lines: int = _DETAIL_LINES, prefix: str = "") -> str:
    rows = str(text or "").splitlines()
    shown = [prefix + row for row in rows[:lines]]
    if len(rows) > lines:
        shown.append(f"{prefix}… ({len(rows) - lines} more lines)")
    return "\n".join(shown)


def _compact(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)
    except (TypeError, ValueError):
        return str(value)


def _pretty(value: Any) -> str:
    try:
        return _head(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    except (TypeError, ValueError):
        return _head(str(value))


def summarize(tool: str, tool_input: Any, cwd: str = "") -> Tuple[str, str, str]:
    """Return ``(title, args, detail)`` for a tool call.

    ``args`` is one line for the pill and session rows; ``detail`` is the
    multi-line version the permission card shows (full command, diff excerpt…).
    """
    name = str(tool or "Tool")
    data: Dict[str, Any] = tool_input if isinstance(tool_input, dict) else {}

    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1] if len(parts) > 1 and parts[1] else "mcp"
        function = "__".join(parts[2:]) or server
        return f"{server} · {function}", one_line(_compact(data), 300), _pretty(data)

    if name in ("Bash", "PowerShell"):
        command = str(data.get("command") or "")
        description = str(data.get("description") or "").strip()
        detail = _head(command.strip())
        if description:
            detail += f"\n# {one_line(description, 200)}"
        return name, one_line(command, 300), detail

    path = _rel(str(data.get("file_path") or data.get("notebook_path") or data.get("path") or ""), cwd)
    if name == "Edit":
        old = _head(str(data.get("old_string") or ""), 6, "- ")
        new = _head(str(data.get("new_string") or ""), 6, "+ ")
        return name, path, "\n".join(part for part in (old, new) if part)
    if name == "MultiEdit":
        edits = data.get("edits") if isinstance(data.get("edits"), list) else []
        return name, path, f"{len(edits)} edits in {path}"
    if name == "Write":
        return name, path, _head(str(data.get("content") or ""))
    if name in ("Read", "NotebookEdit", "NotebookRead"):
        return name, path, path
    if name in ("Glob", "Grep"):
        pattern = str(data.get("pattern") or "")
        where = f"  in {path}" if path else ""
        return name, one_line(pattern + where, 300), pattern + where
    if name == "WebFetch":
        url = str(data.get("url") or "")
        return name, url, "\n".join(x for x in (url, one_line(data.get("prompt"), 200)) if x)
    if name == "WebSearch":
        query = str(data.get("query") or "")
        return name, one_line(query, 300), query
    if name in ("Task", "Agent"):
        what = str(data.get("description") or data.get("subagent_type") or "")
        return name, one_line(what, 300), _head(str(data.get("prompt") or what))
    if name == "TodoWrite":
        todos = data.get("todos") if isinstance(data.get("todos"), list) else []
        return name, f"{len(todos)} todos", ""
    return name, one_line(_compact(data), 300), _pretty(data)


def describe_suggestions(suggestions: Any) -> str:
    """Human label for Claude Code's "don't ask again" permission suggestions."""
    rules = []
    for suggestion in suggestions if isinstance(suggestions, list) else []:
        if not isinstance(suggestion, dict):
            continue
        if suggestion.get("type") in ("addRules", "replaceRules"):
            for rule in suggestion.get("rules") or []:
                if isinstance(rule, dict) and rule.get("toolName"):
                    content = rule.get("ruleContent")
                    rules.append(f"{rule['toolName']}({content})" if content else str(rule["toolName"]))
        elif suggestion.get("type") == "addDirectories":
            for directory in suggestion.get("directories") or []:
                rules.append(f"files in {os.path.basename(str(directory).rstrip('/')) or directory}")
        elif suggestion.get("type") == "setMode" and suggestion.get("mode"):
            rules.append(f"mode: {suggestion['mode']}")
    return one_line(", ".join(rules), 120)
