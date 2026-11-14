"""Agent catalog — per-agent identity (icon + accent color) for any AI CLI.

Dulus Bar is agent-agnostic: it will happily show *any* agent name a wrapper
reports. This module just gives the well-known ones a recognizable emoji and
accent color, and hands unknown agents a stable, nicely-distributed color so
the UI still looks intentional. It is presentation-only.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class AgentStyle:
    key: str
    display: str
    emoji: str
    accent: str  # hex
    aliases: tuple = ()


# Curated identities. `accent` roughly follows each brand.
_AGENTS: List[AgentStyle] = [
    AgentStyle("dulus", "Dulus", "🦅", "#f59e0b", ("dulus", "interant")),
    AgentStyle("claude", "Claude Code", "✳️", "#d97757", ("claude", "claude code", "anthropic")),
    AgentStyle("codex", "Codex", "◆", "#10a37f", ("codex", "openai", "gpt")),
    AgentStyle("gemini", "Gemini", "✦", "#4285f4", ("gemini", "google")),
    AgentStyle("cursor", "Cursor", "▸", "#7c3aed", ("cursor",)),
    AgentStyle("copilot", "Copilot", "⧉", "#6e7681", ("copilot", "github copilot")),
    AgentStyle("kimi", "Kimi", "◐", "#2563eb", ("kimi", "moonshot")),
    AgentStyle("deepseek", "DeepSeek", "≋", "#4d6bfe", ("deepseek",)),
    AgentStyle("grok", "Grok", "𝕏", "#111827", ("grok", "xai")),
    AgentStyle("qwen", "Qwen", "✺", "#615ced", ("qwen", "alibaba", "tongyi")),
    AgentStyle("mistral", "Mistral", "◢", "#fa5010", ("mistral", "codestral")),
    AgentStyle("windsurf", "Windsurf", "≈", "#09b6a2", ("windsurf", "codeium")),
    AgentStyle("zed", "Zed", "⚡", "#084ccf", ("zed",)),
    AgentStyle("aider", "Aider", "✎", "#22c55e", ("aider",)),
    AgentStyle("cline", "Cline", "◔", "#0ea5e9", ("cline", "roo")),
    AgentStyle("ollama", "Ollama", "◍", "#000000", ("ollama", "llama")),
    AgentStyle("opencode", "OpenCode", "◈", "#f97316", ("opencode",)),
    AgentStyle("goose", "Goose", "🪿", "#8b5cf6", ("goose", "block goose")),
    AgentStyle("amp", "Amp", "⚡︎", "#ff2d20", ("amp", "sourcegraph")),
    AgentStyle("crush", "Crush", "♥", "#ec4899", ("crush", "charm")),
    AgentStyle("auggie", "Auggie", "Ⓐ", "#0d9488", ("auggie", "augment")),
    AgentStyle("continue", "Continue", "▶", "#ffde59", ("continue", "cn")),
    AgentStyle("plandex", "Plandex", "⌘", "#84cc16", ("plandex",)),
]

_BY_ALIAS: Dict[str, AgentStyle] = {}
for _a in _AGENTS:
    _BY_ALIAS[_a.key] = _a
    for _al in _a.aliases:
        _BY_ALIAS[_al] = _a

# Fallback palette for unknown agents (kept colorblind-distinct-ish).
_FALLBACK_PALETTE = (
    "#38bdf8",
    "#a78bfa",
    "#34d399",
    "#fbbf24",
    "#fb7185",
    "#e879f9",
    "#5eead4",
    "#93c5fd",
)


def _mentions(alias: str, name: str) -> bool:
    """``alias`` appears in ``name``. Short ones ("cn", "amp", "zed", "gpt"…)
    must be a whole word: "Example Agent" is not Amp, "Organized" is not Zed."""
    if len(alias) > 3:
        return alias in name
    return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", name) is not None


def style_for(agent_name: str) -> AgentStyle:
    """Return an AgentStyle for any agent name (never raises)."""
    name = (agent_name or "").strip()
    low = name.lower()
    if low in _BY_ALIAS:
        return _BY_ALIAS[low]
    for alias, style in _BY_ALIAS.items():
        if _mentions(alias, low):
            return style
    # Deterministic fallback color from the name.
    idx = sum(ord(c) for c in low) % len(_FALLBACK_PALETTE) if low else 0
    return AgentStyle(
        key=low or "agent",
        display=name or "Agent",
        emoji="●",
        accent=_FALLBACK_PALETTE[idx],
    )


def is_dulus(agent_name: str) -> bool:
    return style_for(agent_name).key == "dulus"


def readable_accent(color: str) -> str:
    """``color`` (#rrggbb), mixed 60% toward white when its luminance is below
    0.4: Grok's near-black and Ollama's black vanish on the dark island."""
    try:
        red, green, blue = (int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return color
    if (0.299 * red + 0.587 * green + 0.114 * blue) / 255 >= 0.4:
        return color
    return "#" + "".join(f"{round(c + (255 - c) * 0.6):02x}" for c in (red, green, blue))


# CLI executable names for known agents, used by "quick launch" in the UI.
# The first name found on PATH wins.
_LAUNCH_COMMANDS: Dict[str, Tuple[str, ...]] = {
    "claude": ("claude",),
    "codex": ("codex",),
    "gemini": ("gemini",),
    "cursor": ("cursor-agent", "cursor"),
    "aider": ("aider",),
    "qwen": ("qwen",),
    "grok": ("grok",),
    "copilot": ("copilot",),
    "opencode": ("opencode",),
    "goose": ("goose",),
    "amp": ("amp",),
    "crush": ("crush",),
    "auggie": ("auggie",),
    "continue": ("cn", "continue"),
    "plandex": ("plandex",),
    "kimi": ("kimi",),
    "mistral": ("mistral", "codestral"),
}


def detect_installed() -> List[Tuple[AgentStyle, str]]:
    """Return (style, resolved_executable) for known agents found on PATH."""
    found: List[Tuple[AgentStyle, str]] = []
    for key, exe_names in _LAUNCH_COMMANDS.items():
        for exe in exe_names:
            path = shutil.which(exe)
            if path:
                found.append((_BY_ALIAS[key], path))
                break
    return found
