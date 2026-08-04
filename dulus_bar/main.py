"""Entry point for Dulus Bar.

Supports both:
  python -m dulus_bar
  python dulus_bar/main.py

A menu-bar / tray app must never hold a terminal hostage. When launched from a
console (``dulusbar`` / ``dulus-bar`` in a shell) we relaunch ourselves detached
with no console and return immediately, so the prompt comes straight back and
the app lives in the tray / status bar.
"""

import os
import sys


def _launched_from_console() -> bool:
    """True when we're attached to a terminal we should escape from."""
    if os.name == "nt":
        try:
            import ctypes
            return bool(ctypes.windll.kernel32.GetConsoleWindow())
        except Exception:
            return False
    try:
        return bool(sys.stdout and sys.stdout.isatty())
    except Exception:
        return False


def _relaunch_detached() -> bool:
    """Relaunch in the background with no console. Returns True on success."""
    import subprocess

    env = dict(os.environ)
    env["DULUS_BAR_DETACHED"] = "1"
    argv = [sys.executable, "-m", "dulus_bar"]
    try:
        if os.name == "nt":
            DETACHED_PROCESS = 0x00000008
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                argv,
                env=env,
                creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            subprocess.Popen(
                argv,
                env=env,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return True
    except Exception:
        return False


def _run() -> None:
    try:
        from dulus_bar.macos_native import run_native, should_use_native

        if should_use_native():
            try:
                raise SystemExit(run_native())
            except Exception as exc:
                if os.environ.get("DULUS_BAR_NATIVE_REQUIRED", "0") in {"1", "true", "yes"}:
                    raise
                print(
                    f"[dulusbar] native macOS surface unavailable ({exc}); using Qt fallback",
                    file=sys.stderr,
                )

        from dulus_bar.overlay import run_overlay

        run_overlay()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Dulus Bar crashed: {exc}", file=sys.stderr)
        raise


def main():
    # Escape the terminal on first launch (unless already detached, or the user
    # forces a foreground run with DULUS_BAR_FOREGROUND=1 for debugging).
    if (
        os.environ.get("DULUS_BAR_DETACHED") != "1"
        and os.environ.get("DULUS_BAR_FOREGROUND", "0") not in {"1", "true", "yes"}
        and _launched_from_console()
    ):
        if _relaunch_detached():
            return  # the detached copy took over — free this terminal
    _run()


if __name__ == "__main__":
    if __package__ is None:
        # Running directly (python dulus_bar/main.py): add project root to path.
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, project_root)
    main()
