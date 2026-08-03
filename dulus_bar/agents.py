"""Agent catalog — per-agent identity (icon + accent color) for any AI CLI.

Dulus Bar is agent-agnostic: it will happily show *any* agent name a wrapper
reports. This module just gives the well-known ones a recognizable emoji and
accent color, and hands unknown agents a stable, nicely-distributed color so
the UI still looks intentional. It is presentation-only.
"""

from __future__ import annotations

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


def style_for(agent_name: str) -> AgentStyle:
    """Return an AgentStyle for any agent name (never raises)."""
    name = (agent_name or "").strip()
    low = name.lower()
    if low in _BY_ALIAS:
        return _BY_ALIAS[low]
    for alias, style in _BY_ALIAS.items():
        if alias in low:
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
