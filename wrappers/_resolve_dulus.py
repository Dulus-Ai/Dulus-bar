"""CLI helper used by connect.ps1. Prints: HOW\\nPATH (or HOW\\nCMD0 if path entry)."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from paths import resolve_dulus_command, save_dulus_path  # noqa: E402


def main() -> int:
    try:
        cmd, how = resolve_dulus_command()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(how)
    # Prefer the dulus.py path when present
    if len(cmd) >= 2 and str(cmd[1]).lower().endswith("dulus.py"):
        p = Path(cmd[1])
        save_dulus_path(p)
        print(str(p))
    else:
        print(cmd[0] if cmd else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
