"""Lifecycle bridge for the native SwiftUI/AppKit notch surface on macOS."""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .server import AgentEventServer

WS_PORT = 17372
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
    if not native_available():
        if os.environ.get("DULUS_BAR_NATIVE_AUTOBUILD", "1") == "0":
            raise FileNotFoundError(f"native helper not built: {binary}")
        print("[dulusbar] native helper missing; building release binary...")
        binary = build_native()

    server = AgentEventServer(port=WS_PORT)
    server.start()
    process = subprocess.Popen(
        [str(binary), "--websocket", f"ws://127.0.0.1:{WS_PORT}"],
        cwd=project_root(),
        env={**os.environ, "DULUS_BAR_PARENT_PID": str(os.getpid())},
    )

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
        while True:
            code = process.poll()
            if code is not None:
                return code
            time.sleep(0.25)
    finally:
        cleanup()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def should_use_native() -> bool:
    if sys.platform != "darwin":
        return False
    return os.environ.get("DULUS_BAR_FORCE_QT", "0").lower() not in {"1", "true", "yes"}
