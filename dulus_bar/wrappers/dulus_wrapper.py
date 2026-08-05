"""Wrapper for Dulus (Interant private / local source).

Usage:
    python dulus_wrapper.py [args passed to dulus]
    python dulus_wrapper.py --dulus "C:\\path\\to\\dulus.py" [args]
    dulus-vibe [args]          # after setup.ps1 / connect.cmd

Resolves dulus.py automatically from:
  Desktop\\Interant-master - FINAL, env VIBE_DULUS_PATH, dulus_path.txt, PATH.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from base_wrapper import AgentWrapper  # noqa: E402
from paths import resolve_dulus_command, save_dulus_path  # noqa: E402


def _split_args(argv: List[str]) -> Tuple[Optional[str], List[str]]:
    """Parse optional --dulus PATH, return (override_path, remaining_args)."""
    override: Optional[str] = None
    out: List[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--dulus", "--dulus-path") and i + 1 < len(argv):
            override = argv[i + 1]
            i += 2
            continue
        if a.startswith("--dulus="):
            override = a.split("=", 1)[1]
            i += 1
            continue
        if a in ("-h", "--help") and not out:
            _print_help()
            sys.exit(0)
        out.append(a)
        i += 1
    return override, out


def _initial_model(args: List[str]) -> str:
    """Best-effort read of the model from Dulus args (`-m X` / `--model X`)."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-m", "--model") and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("-m=") or a.startswith("--model="):
            return a.split("=", 1)[1]
        i += 1
    return ""


def _print_help() -> None:
    print(
        """dulus_wrapper — lanza Dulus conectado a Dulus Bar

Uso:
  python dulus_wrapper.py [dulus args...]
  python dulus_wrapper.py --dulus "C:\\ruta\\dulus.py" [args...]

Primero abre el Island (connect.cmd o launch.bat), luego corre esto.

Ejemplos:
  python dulus_wrapper.py
  python dulus_wrapper.py "arregla el login"
  python dulus_wrapper.py -m kimi/kimi-k2.5
  python dulus_wrapper.py --dulus "C:\\Users\\KevRojo\\Desktop\\Interant-master - FINAL\\dulus.py"
"""
    )


async def main() -> int:
    override, agent_args = _split_args(sys.argv[1:])

    if override:
        p = Path(override).expanduser()
        if p.is_dir():
            p = p / "dulus.py"
        if not p.is_file():
            print(f"[dulus-bar] no existe: {p}", file=sys.stderr)
            return 1
        save_dulus_path(p)
        command = ["python", str(p.resolve()), *agent_args]
        how = f"flag:{p}"
    else:
        try:
            base, how = resolve_dulus_command()
        except FileNotFoundError as exc:
            print(f"[dulus-bar] {exc}", file=sys.stderr)
            return 1
        command = [*base, *agent_args]
        # remember successful auto-detect
        if len(base) >= 2 and base[1].lower().endswith("dulus.py"):
            try:
                save_dulus_path(Path(base[1]))
            except Exception:
                pass

    print(f"[dulus-bar] Dulus via {how}", file=sys.stderr)
    print(f"[dulus-bar] exec: {' '.join(command[:3])}{' ...' if len(command) > 3 else ''}", file=sys.stderr)

    wrapper = AgentWrapper("Dulus", command, model=_initial_model(agent_args))
    # Dulus-specific permission cues (ApprovalRuntime + CLI prompts)
    import re

    # Live model / ctx scraping from Dulus stdout — Dulus Bar renders these.
    wrapper.model_patterns.extend(
        [
            re.compile(r"\bmodel\b\s*[:=]\s*([\w./:\-]+)", re.I),
            re.compile(r"\busing\s+model\s+([\w./:\-]+)", re.I),
        ]
    )
    wrapper.ctx_patterns.extend(
        [
            re.compile(r"\bctx\b\s*[:=]?\s*(\d+\s*%)", re.I),
            re.compile(r"\bcontext\b\s*[:=]?\s*(\d+\s*%)", re.I),
            re.compile(r"(\d+\s*%)\s*(?:of\s+)?context", re.I),
            re.compile(r"(\d+[kKmM]?\s*/\s*\d+[kKmM]?)\s*tokens", re.I),
        ]
    )

    wrapper.permission_patterns.extend(
        [
            re.compile(r"Allow\s+this\s+action", re.I),
            re.compile(r"Approve\s+tool", re.I),
            re.compile(r"permission\s+required", re.I),
            re.compile(r"Waiting\s+for\s+approval", re.I),
            re.compile(r"\[A\]pprove", re.I),
            re.compile(r"approve\s*/\s*reject", re.I),
            re.compile(r"Run\s+this\s+tool", re.I),
            re.compile(r"Tool\s+call\s+requires", re.I),
        ]
    )
    wrapper.done_patterns.extend(
        [
            re.compile(r"session\s+ended", re.I),
            re.compile(r"goodbye", re.I),
        ]
    )
    return await wrapper.run()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
