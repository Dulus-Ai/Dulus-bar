#!/usr/bin/env bash
# setup.sh (macOS / Linux) — install deps and make ./dulusbar executable.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"

echo "[setup] Dulus Bar root: $ROOT"
command -v "$PY" >/dev/null 2>&1 || { echo "[setup] $PY not found"; exit 1; }

echo "[setup] installing dependencies..."
"$PY" -m pip install -r requirements.txt
"$PY" -m pip install -e . || echo "[setup] editable install skipped (PYTHONPATH still works)"

chmod +x "$ROOT/dulusbar" || true

# macOS gets a native SwiftUI/AppKit notch surface. Python remains the agent
# event hub; PyQt is retained as a safe fallback and for Windows/Linux.
if [[ "$(uname -s)" == "Darwin" ]]; then
  if command -v swift >/dev/null 2>&1; then
    echo "[setup] building native macOS notch surface..."
    swift build -c release --package-path "$ROOT/macos"
  else
    echo "[setup] warning: Swift toolchain not found; macOS will use the Qt fallback."
    echo "[setup] install Xcode Command Line Tools with: xcode-select --install"
  fi
fi

# Linux window activation needs wmctrl or xdotool for jump-to-terminal.
if [[ "$(uname -s)" == "Linux" ]]; then
  if ! command -v wmctrl >/dev/null 2>&1 && ! command -v xdotool >/dev/null 2>&1; then
    echo "[setup] tip: install 'wmctrl' or 'xdotool' for click-to-focus terminal (X11)."
  fi
fi

echo ""
echo "[setup] done. Run:"
echo "  ./dulusbar               # bar + Dulus"
echo "  ./dulusbar --island-only # just the bar"
echo "  dulusbar                 # (if pip -e install succeeded) just the bar"
