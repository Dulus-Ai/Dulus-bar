"""Tiny, dependency-free text helpers (safe for the hook hot path)."""

from __future__ import annotations

from typing import Any


def one_line(text: Any, limit: int = 120) -> str:
    """Collapse whitespace to one line and truncate with an ellipsis."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def first_line(text: Any, limit: int = 140) -> str:
    """First meaningful line of an agent's reply, minus markdown decoration."""
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("#>-*• ").strip()
        line = line.replace("**", "").replace("__", "").replace("`", "").strip()
        if line:
            return one_line(line, limit)
    return ""


def call_text(tool: str, args: str, sep: str = "  ") -> str:
    """ONE clean representation of a tool call.

    Guards against clients that stuff (almost) the same string into both
    ``tool`` and ``args`` — the same call is never rendered twice.
    """
    tool = (tool or "").strip()
    args = (args or "").strip()
    if not args:
        return tool
    if not tool:
        return args
    if tool in args or args in tool:
        return args if len(args) >= len(tool) else tool
    return f"{tool}{sep}{args}"
