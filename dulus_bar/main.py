"""Entry point for Dulus Bar.

Supports both:
  python -m dulus_bar
  python dulus_bar/main.py
"""

import os
import sys


def main():
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


if __name__ == "__main__":
    if __package__ is None:
        # Running directly (python dulus_bar/main.py): add project root to path.
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, project_root)
    main()
