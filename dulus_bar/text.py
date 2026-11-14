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


def _hides_text(code: int) -> bool:
    """Controls, and the bidi overrides / isolates that reorder what's shown."""
    return (
        code < 0x20 or 0x7F <= code < 0xA0
        or code in (0x061C, 0x200E, 0x200F)
        or 0x202A <= code <= 0x202E or 0x2066 <= code <= 0x2069
    )


def reveal_hidden(text: Any) -> str:
    """Show characters that hide or reorder text as ``⟨U+202E⟩``.

    For approval cards: a right-to-left override can make a command *read*
    differently than it *runs* (``rm -rf ~/x`` displayed as something
    harmless). What you approve must read exactly as it runs. Newlines and
    tabs are kept; Windows line endings are just line endings.
    """
    source = str(text or "").replace("\r\n", "\n")
    return "".join(
        f"⟨U+{ord(ch):04X}⟩" if ch not in "\n\t" and _hides_text(ord(ch)) else ch for ch in source
    )


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
