"""Generic wrapper for any AI agent CLI.

Usage:
    python agent_wrapper.py "Cursor Agent" cursor [cursor args...]
    python agent_wrapper.py "Codex" codex [codex args...]
    python agent_wrapper.py "Kimi Code" kimi [kimi args...]
"""

import asyncio
import os
import sys

# Add repo root to path so base_wrapper can be imported
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from base_wrapper import AgentWrapper  # noqa: E402


async def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: agent_wrapper.py \"Agent Name\" command [args...]", file=sys.stderr)
        return 1
    agent_name = sys.argv[1]
    command = sys.argv[2:]
    wrapper = AgentWrapper(agent_name, command)
    return await wrapper.run()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
