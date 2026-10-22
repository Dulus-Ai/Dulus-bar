"""Wire third-party agents (Claude Code, Codex) to the island.

Helpers shared by the integrations: how to invoke *this* installation from
another program's config (plain Python or a frozen Nuitka / PyInstaller
binary), shell quoting for the shell that program will use, and careful JSON
config editing (backup, atomic write, never clobber a file we can't parse).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

PACKAGE_DIR = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """A config file we were asked to edit is unreadable or unexpected."""


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def frozen_executable() -> str:
    compiled = globals().get("__compiled__")
    # Nuitka onefile unpacks to a temp dir; original_argv0 is the real binary.
    for candidate in (getattr(compiled, "original_argv0", None), sys.executable):
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return sys.executable


def python_console() -> str:
    """A python with real stdio (pythonw.exe has none)."""
    import shutil

    exe = sys.executable or "python"
    if exe.lower().endswith("pythonw.exe"):
        candidate = exe[: -len("pythonw.exe")] + "python.exe"
        if Path(candidate).exists():
            return candidate
        return shutil.which("python") or "python"
    return exe


def self_argv(*args: str) -> List[str]:
    """argv that runs ``dulusbar <args>`` with this very installation.

    Frozen builds re-invoke themselves; source and pip installs run
    ``main.py`` by absolute path, which works without being on PYTHONPATH.
    """
    if is_frozen():
        return [frozen_executable(), *args]
    return [python_console(), str(PACKAGE_DIR / "main.py"), *args]


def windows_hook_shell() -> str:
    """Which shell Claude Code will run hook commands with on Windows."""
    import shutil

    if os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"):
        return "bash"
    found = shutil.which("bash") or ""
    if found and "system32" not in found.lower():  # that one is WSL's launcher
        return "bash"
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if root and os.path.exists(os.path.join(root, "Git", "bin", "bash.exe")):
            return "bash"
    return "powershell"


def shell_command(argv: List[str], shell: str = "bash") -> str:
    """Quote argv for the shell that will run it."""
    import shlex

    if shell == "powershell":
        return "& " + " ".join("'" + a.replace("'", "''") + "'" for a in argv)
    if os.name == "nt":  # Git Bash: forward slashes, POSIX quoting
        argv = [a.replace("\\", "/") for a in argv]
    return " ".join(shlex.quote(a) for a in argv)


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"can't read {path}: {exc}") from exc
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigError(f"{path} is not valid JSON ({exc}); fix it first, nothing was changed") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} does not contain a JSON object; nothing was changed")
    return data


def _target(path: Path) -> Path:
    """Write *through* a symlink (dotfiles repos), never over it."""
    try:
        return path.resolve() if path.is_symlink() else path
    except OSError:
        return path


def write_text(path: Path, text: str) -> None:
    """Back up the old file, then replace it atomically — keeping its
    permissions (settings.json can hold API keys) and any symlink."""
    import stat

    target = _target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup(target)
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        mode = 0o600  # a new file: only you
    tmp = target.with_name(target.name + ".dulus-bar.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.chmod(tmp, mode)  # O_CREAT is filtered by the umask
    os.replace(tmp, target)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def backup(path: Path) -> None:
    import shutil

    if path.exists():
        try:
            shutil.copy2(path, path.with_name(path.name + ".dulus-bar.bak"))
        except OSError:
            pass
