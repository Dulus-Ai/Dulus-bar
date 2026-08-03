"""Resolve the local Dulus source path for Dulus Bar without hardcoding.

Order of preference for Dulus:
  1. env DULUS_BAR_PATH / VIBE_DULUS_PATH / DULUS_PATH (file or directory)
  2. config file next to the repo: ../dulus_path.txt
  3. user config: ~/.dulus/dulus_bar_dulus_path.txt (or the legacy
     vibe_island_dulus_path.txt)
  4. known local locations (Interant-master - FINAL, etc.)
  5. `dulus` on PATH (pip install)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent  # repo root (parent of wrappers/)

# Common relative locations for a local Dulus checkout, resolved against the
# current user's home (no hardcoded usernames — portable across machines).
_HOME = Path(os.path.expanduser("~"))
_KNOWN_DULUS_RELATIVE = [
    "Desktop/Interant-master - FINAL/dulus.py",
    "Desktop/Interant-master - FINAL",
    "Desktop/Dulus-public/dulus.py",
    "Desktop/deploys/INTERANT-unified/dulus.py",
    "dulus/dulus.py",
    "Dulus/dulus.py",
]
_KNOWN_DULUS_CANDIDATES = [_HOME / rel for rel in _KNOWN_DULUS_RELATIVE]


def island_root() -> Path:
    return _ROOT


def config_paths() -> List[Path]:
    home = Path(os.path.expanduser("~"))
    return [
        _ROOT / "dulus_path.txt",
        home / ".dulus" / "dulus_bar_dulus_path.txt",
        home / ".dulus" / "vibe_island_dulus_path.txt",  # legacy
        home / ".dulus" / "dulus_path.txt",
    ]


def _read_config_line(path: Path) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().strip('"').strip("'")
            if line and not line.startswith("#"):
                return line
    except OSError:
        return None
    return None


def save_dulus_path(path: Path) -> Path:
    """Persist the chosen Dulus path so next time it's automatic."""
    path = path.resolve()
    targets = [
        _ROOT / "dulus_path.txt",
        Path(os.path.expanduser("~")) / ".dulus" / "dulus_bar_dulus_path.txt",
    ]
    text = str(path) + "\n"
    written = targets[0]
    for t in targets:
        try:
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(text, encoding="utf-8")
            written = t
        except OSError:
            continue
    return written


def _as_command(candidate: Path) -> Optional[List[str]]:
    """Turn a path into an argv to launch Dulus."""
    if not candidate:
        return None
    try:
        p = candidate.expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        else:
            p = p.resolve()
    except OSError:
        return None

    if p.is_file() and p.suffix.lower() == ".py":
        return ["python", str(p)]
    if p.is_dir():
        dulus_py = p / "dulus.py"
        if dulus_py.is_file():
            return ["python", str(dulus_py)]
    return None


def resolve_dulus_command() -> Tuple[List[str], str]:
    """Return (argv, how_we_found_it).

    Raises FileNotFoundError if nothing works.
    """
    # 1) env
    for env_key in ("DULUS_BAR_PATH", "VIBE_DULUS_PATH", "DULUS_PATH", "DULUS_HOME"):
        raw = os.environ.get(env_key, "").strip().strip('"')
        if raw:
            cmd = _as_command(Path(raw))
            if cmd:
                return cmd, f"env:{env_key}"

    # 2) config files
    for cfg in config_paths():
        raw = _read_config_line(cfg)
        if raw:
            cmd = _as_command(Path(raw))
            if cmd:
                return cmd, f"config:{cfg}"

    # 3) known Desktop locations
    for cand in _KNOWN_DULUS_CANDIDATES:
        cmd = _as_command(cand)
        if cmd:
            return cmd, f"known:{cand}"

    # 4) dulus on PATH
    which = shutil.which("dulus") or shutil.which("dulus.exe")
    if which:
        return [which], f"path:{which}"

    # 5) fuzzy scan Desktop for dulus.py near "Interant" / "FINAL"
    desktop = Path(os.path.expanduser("~")) / "Desktop"
    if desktop.is_dir():
        try:
            hits = sorted(
                desktop.rglob("dulus.py"),
                key=lambda p: (
                    0 if "final" in str(p).lower() else 1,
                    0 if "interant" in str(p).lower() else 1,
                    len(str(p)),
                ),
            )
            # keep it cheap — only first few
            for hit in hits[:12]:
                # skip obvious junk
                s = str(hit).lower()
                if any(x in s for x in ("\\node_modules\\", "\\.git\\", "\\venv\\", "\\.venv\\")):
                    continue
                cmd = _as_command(hit)
                if cmd:
                    return cmd, f"scan:{hit}"
        except OSError:
            pass

    raise FileNotFoundError(
        "No encontré dulus.py.\n"
        "Pon la ruta en una de estas:\n"
        f"  - {_ROOT / 'dulus_path.txt'}\n"
        f"  - {Path(os.path.expanduser('~')) / '.dulus' / 'vibe_island_dulus_path.txt'}\n"
        "  - env VIBE_DULUS_PATH\n"
        "O pásala:  python wrappers\\dulus_wrapper.py --dulus \"C:\\\\ruta\\\\dulus.py\" ..."
    )


def default_dulus_source() -> Path:
    """Best-guess path for setup scripts (may not exist)."""
    try:
        cmd, _ = resolve_dulus_command()
        if len(cmd) >= 2 and cmd[0].lower().startswith("python"):
            return Path(cmd[1])
        return Path(cmd[0])
    except FileNotFoundError:
        return _KNOWN_DULUS_CANDIDATES[0]
