"""Lifecycle bridge for the native SwiftUI/AppKit notch surface on macOS."""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from . import protocol as P
from .server import AgentEventServer

_NATIVE_EXECUTABLE = "DulusBarNative"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def native_binary(root: Optional[Path] = None) -> Path:
    """Return the preferred release helper path."""
    base = root or project_root()
    override = os.environ.get("DULUS_BAR_NATIVE_BINARY")
    if override:
        return Path(override).expanduser().resolve()
    return base / "macos" / ".build" / "release" / _NATIVE_EXECUTABLE


def native_available(root: Optional[Path] = None) -> bool:
    path = native_binary(root)
    return path.is_file() and os.access(path, os.X_OK)


def _sources(root: Path) -> List[Path]:
    package = root / "macos"
    return [package / "Package.swift", *sorted((package / "Sources").rglob("*"))]


def native_stale(root: Optional[Path] = None) -> bool:
    """True when the built helper predates its sources (e.g. after git pull).

    A stale helper speaks an older protocol, so it would sit there empty.
    """
    if os.environ.get("DULUS_BAR_NATIVE_BINARY"):
        return False  # a binary the user pointed us at: their call
    base = root or project_root()
    binary = native_binary(base)
    if not binary.is_file():
        return False
    built = binary.stat().st_mtime
    return any(p.is_file() and p.stat().st_mtime > built for p in _sources(base))


def build_native(root: Optional[Path] = None) -> Path:
    """Build the native helper and return its executable path."""
    base = root or project_root()
    package = base / "macos"
    if not (package / "Package.swift").is_file():
        raise FileNotFoundError(f"native package missing: {package}")
    subprocess.run(
        ["swift", "build", "-c", "release"],
        cwd=package,
        check=True,
    )
    binary = native_binary(base)
    if not binary.is_file():
        raise FileNotFoundError(f"native build completed without {binary}")
    return binary


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
    if not native_available() or native_stale():
        if os.environ.get("DULUS_BAR_NATIVE_AUTOBUILD", "1") == "0":
            state = "out of date" if native_available() else "not built"
            raise FileNotFoundError(
                f"native helper {state}: {binary} (swift build -c release --package-path macos)"
            )
        print("[dulusbar] building the native macOS island…")
        binary = build_native()

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
            # The UI token travels in the environment, never on the command
            # line where any local user could read it with `ps`.
            env={**os.environ, "DULUS_BAR_PARENT_PID": str(os.getpid()), "DULUS_BAR_TOKEN": server.token},
        )
    except Exception:
        server.stop()  # free the port for the Qt fallback
        raise

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
