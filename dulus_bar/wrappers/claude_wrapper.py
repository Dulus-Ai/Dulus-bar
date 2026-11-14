"""Wrapper for Claude Code using the shared base wrapper.

Usage:
    python claude_wrapper.py [args passed to claude]

Install alias in PowerShell:
    function claude { python C:\\path\\to\\claude_wrapper.py @args }

With the Claude Code hooks installed (``dulusbar hooks install claude``) the
hooks already report everything — straight from Claude Code, Allow / Deny
included — so this just runs ``claude`` instead of adding a duplicate session.
"""

import asyncio
import os
import shutil
import sys

# Add repo root to path so base_wrapper can be imported
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from base_wrapper import AgentWrapper  # noqa: E402


def resolve_claude() -> str:
    for name in ["claude", "claude.exe"]:
        path = shutil.which(name)
        if path:
            return path
    return "claude"


async def main() -> int:
    agent = AgentWrapper("Claude Code", [resolve_claude(), *sys.argv[1:]])
    # Claude Code's dialog is a menu: Enter takes the default "Yes", Esc is "No".
    agent.answer_yes_pty = agent.answer_yes_pipe = "\r"
    agent.answer_no_pty = agent.answer_no_pipe = "\x1b"
    return await agent.run()  # runs claude directly when its hooks are installed


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
