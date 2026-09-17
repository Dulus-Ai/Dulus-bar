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
  # Wayland clients cannot position their own window, so the island runs through
  # XWayland to stay pinned at the top edge instead of drifting to the centre.
  if [[ "${XDG_SESSION_TYPE:-}" == "wayland" || -n "${WAYLAND_DISPLAY:-}" ]] && [[ -z "${DISPLAY:-}" ]]; then
    echo "[setup] warning: Wayland session without XWayland — the compositor will place the island."
    echo "[setup] enable XWayland (or use an X11 session) to pin it to the top of the screen."
  fi
  # Desktop entry + icon so the launcher, window and tray show a real name/icon.
  APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
  if mkdir -p "$APPS_DIR" "$ICONS_DIR" 2>/dev/null; then
    cp "$ROOT/assets/dulusbar.desktop" "$APPS_DIR/dulusbar.desktop" 2>/dev/null || true
    cp "$ROOT/dulus_bar/assets/dulus-bird.png" "$ICONS_DIR/dulusbar.png" 2>/dev/null || true
    echo "[setup] installed desktop entry: $APPS_DIR/dulusbar.desktop"
  fi
fi

echo ""
echo "[setup] done. Run:"
echo "  ./dulusbar               # bar + Dulus"
echo "  ./dulusbar --island-only # just the bar"
echo "  dulusbar                 # (if pip -e install succeeded) just the bar"
