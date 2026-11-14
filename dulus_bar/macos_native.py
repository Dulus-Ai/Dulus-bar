"""Lifecycle bridge for the native SwiftUI/AppKit notch surface on macOS.

The Swift package ships inside ``dulus_bar`` (``dulus_bar/macos``), so a pip
install gets the native island as well as a git checkout does. It's built
once per version of its sources into the user's cache — site-packages is no
place for build products — and this Python process stays the WebSocket hub
the native island talks to.

    python -m dulus_bar.macos_native     # build it now (setup.sh does this)
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import protocol as P
from .server import AgentEventServer

_NATIVE_EXECUTABLE = "DulusBarNative"
_BUILD_DIR = re.compile(r"^native-[0-9a-f]{16}$")
# The cache is per user, shared by every install (a checkout and a pip
# install, two venvs…): another version's build goes only once nobody has
# started it for this long.
_UNUSED_BUILD_SECONDS = 30 * 24 * 3600
# A helper that dies this soon after launch never worked: use the Qt island.
_LAUNCH_GRACE_SECONDS = 5.0


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def package_root() -> Path:
    """The Swift package: Package.swift + Sources."""
    return Path(__file__).resolve().parent / "macos"


def _sources(package: Path) -> List[Path]:
    sources = package / "Sources"
    found = sources.rglob("*") if sources.is_dir() else []
    # Not dotfiles: Finder dropping a .DS_Store must not cost a rebuild.
    files = sorted(p for p in found if p.is_file() and not p.name.startswith("."))
    return [package / "Package.swift", *files]


def source_digest(package: Optional[Path] = None) -> str:
    """Identifies this version of the Swift sources (and so its build)."""
    package = package or package_root()
    digest = hashlib.sha256()
    for path in _sources(package):
        if path.is_file():
            digest.update(path.relative_to(package).as_posix().encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def cache_root() -> Path:
    if os.environ.get("DULUS_BAR_HOME") or sys.platform != "darwin":
        return P.app_dir() / "cache"
    return Path.home() / "Library" / "Caches" / "Dulus Bar"


def build_dir(package: Optional[Path] = None) -> Path:
    """Where this version of the sources is built. A new version gets a new
    directory, so an upgrade can never run a helper built from older sources."""
    return cache_root() / f"native-{source_digest(package)}"


def native_binary(package: Optional[Path] = None) -> Path:
    """The helper to run: the user's override, else the build for these sources."""
    override = os.environ.get("DULUS_BAR_NATIVE_BINARY")
    if override:
        return Path(override).expanduser().resolve()
    return build_dir(package) / "release" / _NATIVE_EXECUTABLE


def native_available(package: Optional[Path] = None) -> bool:
    path = native_binary(package)
    return path.is_file() and os.access(path, os.X_OK)


def swift_toolchain() -> Optional[str]:
    """``swift``, when a real toolchain is installed.

    Without the Xcode Command Line Tools, ``/usr/bin/swift`` is a stub that
    pops up an installer dialog — so ask ``xcode-select`` first.
    """
    swift = shutil.which("swift")
    if swift is None:
        return None
    if sys.platform == "darwin":
        try:
            found = subprocess.run(["xcode-select", "-p"], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        if found.returncode != 0:
            return None
    return swift


def _prune_builds(keep: Path, now: Optional[float] = None) -> None:
    """Drop builds of other source versions nobody has started in a month
    (each one is a few MB). Recent ones may belong to another install."""
    now = time.time() if now is None else now
    try:
        for entry in keep.parent.iterdir():
            if entry == keep or not entry.is_dir() or not _BUILD_DIR.match(entry.name):
                continue
            if now - entry.stat().st_mtime > _UNUSED_BUILD_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        pass


def _mark_used(build: Path) -> None:
    """This build was just started: it's not a candidate for pruning."""
    try:
        os.utime(build)
    except OSError:
        pass


def build_native(package: Optional[Path] = None) -> Path:
    """Build the native helper and return its executable path."""
    package = package or package_root()
    if not (package / "Package.swift").is_file():
        raise FileNotFoundError(f"native package missing: {package}")
    swift = swift_toolchain()
    if swift is None:
        raise FileNotFoundError("no Swift toolchain (install the Xcode Command Line Tools: xcode-select --install)")
    scratch = build_dir(package)
    subprocess.run(
        [swift, "build", "-c", "release", "--package-path", str(package), "--scratch-path", str(scratch)],
        check=True,
    )
    binary = scratch / "release" / _NATIVE_EXECUTABLE
    if not binary.is_file():
        raise FileNotFoundError(f"native build completed without {binary}")
    _prune_builds(scratch)
    return binary


def _launch_env(server: AgentEventServer) -> Dict[str, str]:
    from .integrations import is_frozen, python_console, self_argv

    env = {
        **os.environ,
        "DULUS_BAR_PARENT_PID": str(os.getpid()),
        # The UI token travels in the environment, never on the command line
        # where any local user could read it with `ps`.
        "DULUS_BAR_TOKEN": server.token,
        # How the island's menu runs `dulusbar …`: agents it opens are wired
        # to the island (`dulusbar wrap`), and it can connect Claude Code.
        "DULUS_BAR_CLI": json.dumps(self_argv()),
    }
    if not is_frozen():
        wrapper = Path(__file__).resolve().parent / "wrappers" / "dulus_wrapper.py"
        env["DULUS_BAR_OPEN_DULUS"] = json.dumps([python_console(), str(wrapper)])
    return env


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def run_native() -> int:
    """Own the WebSocket hub and run the native helper until it exits."""
    binary = native_binary()
    if not native_available():
        if os.environ.get("DULUS_BAR_NATIVE_BINARY"):
            raise FileNotFoundError(f"DULUS_BAR_NATIVE_BINARY is not an executable: {binary}")
        if os.environ.get("DULUS_BAR_NATIVE_AUTOBUILD", "1") == "0":
            raise FileNotFoundError(f"native helper not built: {binary} (python -m dulus_bar.macos_native)")
        print("[dulusbar] building the native macOS island…")
        binary = build_native()
    if not os.environ.get("DULUS_BAR_NATIVE_BINARY"):
        _mark_used(build_dir())

    port = P.configured_port()
    server = AgentEventServer(port=port, publish=True)
    if not server.start():
        raise RuntimeError(f"the hub could not start on port {port}: {server.error}")
    quit_requested = threading.Event()
    server.on_shutdown(quit_requested.set)
    try:
        process = subprocess.Popen(
            [str(binary), "--websocket", f"ws://{P.DEFAULT_HOST}:{port}"],
            cwd=project_root(),
            env=_launch_env(server),
        )
    except Exception:
        server.stop()  # free the port for the Qt fallback
        raise
    started = time.monotonic()

    cleaned = False

    def cleanup() -> None:
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        _terminate(process)
        server.stop()

    def handle_signal(signum: int, _frame: object) -> None:
        cleanup()
        raise SystemExit(128 + signum)

    atexit.register(cleanup)
    previous_handlers = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, handle_signal)

    try:
        while not quit_requested.is_set():
            code = process.poll()
            if code is not None:
                if code != 0 and time.monotonic() - started < _LAUNCH_GRACE_SECONDS:
                    # Crashed on launch (a broken build, an old macOS): the
                    # caller falls back to the Qt island instead of showing nothing.
                    raise RuntimeError(f"the native island exited at launch (code {code})")
                return code
            time.sleep(0.25)
        return 0
    finally:
        cleanup()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def should_use_native() -> bool:
    if sys.platform != "darwin":
        return False
    return os.environ.get("DULUS_BAR_FORCE_QT", "0").lower() not in {"1", "true", "yes"}


if __name__ == "__main__":
    print(build_native())
