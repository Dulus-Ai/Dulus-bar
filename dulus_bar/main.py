"""Entry point for Dulus Bar.

Supports all of:
  dulusbar [command]            (console script)
  python -m dulus_bar [command]
  python dulus_bar/main.py [command]   (what the Claude Code / Codex hooks run:
                                        works without being on PYTHONPATH)

A menu-bar / tray app must never hold a terminal hostage: started from a
console, the island relaunches itself detached and the prompt comes straight
back (see ``dulus_bar.cli.cmd_run``).
"""

import os
import sys


def main() -> int:
    from dulus_bar.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    frozen = getattr(sys, "frozen", False) or "__compiled__" in globals()
    if __package__ in (None, "") and not frozen:
        # Run by path: make the package importable, and drop this directory
        # from sys.path so our module names can't shadow anything.
        here = os.path.dirname(os.path.abspath(__file__))
        if sys.path and os.path.abspath(sys.path[0] or os.curdir) == here:
            sys.path.pop(0)
        sys.path.insert(0, os.path.dirname(here))
    sys.exit(main())
