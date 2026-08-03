# Dulus Bar 🦅

**A cross-platform Dynamic Island for your AI agents — free, native, no subscription.**

Dulus Bar puts a floating status island at the top of your screen that shows every
AI coding agent you're running — Dulus, Claude Code, Codex, Gemini, Cursor, and any
other CLI — and lets you **Allow / Deny** their permission prompts without diving
back into the terminal.

- **All platforms** — Windows, macOS, and Linux.
- **macOS notch-native** — on MacBooks with a camera notch the island hugs the
  notch and stays visible across every Space and full-screen app.
- **All agents** — anything with a CLI works through a thin wrapper; well-known
  agents get their own icon and accent color.
- **Dulus extras** — for Dulus, the island also shows the active **model** and
  **context usage** live.
- **100% local** — no cloud, no account, no telemetry. One websocket on
  `127.0.0.1:17372`.

---

## What it does

- Floating island, top-center (or notch-anchored on macOS).
- Lists active agents with a live status dot (running / waiting / done / error).
- When an agent asks for permission, shows **Allow / Deny** right on the island and
  forwards your choice back to the agent over stdin.
- Click an agent to jump to its terminal / editor window.
- For **Dulus**: shows model + context (e.g. `kimi/kimi-k2.5 · ctx 38%`).

---

## Install

Requires Python 3.10+.

On macOS, `setup.sh` also builds the native SwiftUI/AppKit notch surface with
the Swift toolchain included in Xcode Command Line Tools. The Python process
remains the local WebSocket hub; Windows and Linux use the PyQt overlay.

### macOS / Linux

```bash
git clone https://bitbucket.org/dulus-ai/dulus-bar.git
cd dulus-bar
./setup.sh          # installs deps, makes ./dulusbar executable
```

> Linux tip: for click-to-focus of a terminal on X11, install `wmctrl` or `xdotool`.

### Windows

```powershell
git clone https://bitbucket.org/dulus-ai/dulus-bar.git
cd dulus-bar
.\setup.ps1         # installs deps + PowerShell aliases
```

### Manual (any OS)

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate   |   Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

---

## Run

### Just the bar

```bash
python -m dulus_bar
# or, after install:
dulusbar
```

### Bar + Dulus, wired together

**macOS / Linux**

```bash
./dulusbar                       # bar + Dulus
./dulusbar "fix the webhook"     # with a prompt
./dulusbar -m kimi/kimi-k2.5     # pick a model (shown on the island)
./dulusbar --island-only         # just the bar
./dulusbar --dulus /path/to/dulus.py
```

**Windows**

```powershell
.\connect.ps1                    # bar + Dulus
.\connect.ps1 "fix the webhook"
.\connect.ps1 -IslandOnly        # just the bar
# or double-click connect.cmd / START_HERE.cmd
```

After `setup.ps1`, Windows also gets aliases: `dulusbar`, `dulusbar-only`,
`dulus-connect`.

### macOS native surface controls

The native surface rests almost entirely inside the physical notch. Hover or
click it to expand; incoming agent activity expands it briefly, and permission
requests stay open until **Allow** or **Deny** is selected. It uses a
non-activating `NSPanel`, so it does not steal focus from your terminal.

Useful diagnostics:

```bash
DULUS_BAR_FORCE_QT=1 python -m dulus_bar       # force cross-platform fallback
DULUS_BAR_NATIVE_REQUIRED=1 python -m dulus_bar # fail instead of falling back
DULUS_BAR_LOG=/tmp/dulusbar.log ./dulusbar --island-only
```

---

## Connect an agent from the island (right-click)

**Right-click the island** for a menu:

- **Open agent…** — pick any agent executable/script from a file dialog; Dulus
  Bar launches it in its own terminal, already wired to the bar. Messages and
  Allow/Deny start flowing instantly.
- **Open Dulus** — launches your resolved `dulus.py` wired up.
- **Quick-launch** — any known agent found on your `PATH` (Claude, Codex,
  Gemini, Cursor, Aider…) appears as a one-click entry.

> Why launch instead of "attach" to an already-running agent? Showing messages
> and injecting Allow/Deny needs the agent's stdio, which only the process that
> spawned it can pipe. So Dulus Bar starts the agent for you (through a thin
> wrapper) rather than trying to hook one that's already running.

## Any AI agent

Dulus Bar is agent-agnostic. Wrap any CLI:

```bash
python wrappers/agent_wrapper.py "Codex" codex fix auth bug
python wrappers/agent_wrapper.py "Gemini" gemini
python wrappers/claude_wrapper.py            # Claude Code helper
```

`agent_wrapper.py "Display Name" <command> [args...]` reports that agent to the bar.
Known names (Claude, Codex, Gemini, Cursor, Copilot, Kimi, DeepSeek, Grok, Qwen,
Mistral, Windsurf, Zed, Aider, Ollama, …) get a curated icon + color; anything else
gets a stable auto-assigned color.

---

## How Dulus is resolved

Dulus Bar finds your local `dulus.py` without hardcoding, in this order:

1. `--dulus PATH` flag, or env `DULUS_BAR_PATH` / `DULUS_PATH`
2. `dulus_path.txt` next to the repo
3. `~/.dulus/dulus_bar_dulus_path.txt`
4. Known local locations
5. `dulus` on `PATH`

The first working path is remembered automatically.

---

## Build a standalone binary (optional)

```bash
pip install pyinstaller
pyinstaller --noconfirm DulusBar.spec
```

Output goes to `dist/` — `DulusBar.exe` on Windows, `DulusBar.app` on macOS
(bundled as an `LSUIElement`, so no Dock icon).

---

## Architecture

```
dulus_bar/
  overlay.py        Floating island UI (PyQt6), notch-aware placement
  server.py         WebSocket hub (ws://127.0.0.1:17372)
  agents.py         Per-agent icon + accent color registry
  native/           Cross-platform native layer
    windows.py        win32 window focus
    macos.py          notch geometry + always-visible NSWindow (pyobjc)
    linux.py          wmctrl / xdotool window focus
wrappers/
  base_wrapper.py   Shared: pipe stdio, detect prompts, inject Allow/Deny
  dulus_wrapper.py  Dulus — also reports model + ctx
  agent_wrapper.py  Generic wrapper for any CLI agent
  paths.py          Auto-resolve dulus.py
```

### WebSocket protocol

Agents → bar:

```json
{ "agent": "Dulus", "type": "message", "session_id": "abc123",
  "payload": { "text": "writing tests...", "model": "kimi/kimi-k2.5", "ctx": "38%" } }
```

Event types: `session_started`, `message`, `tool_request`, `tool_approved`,
`tool_denied`, `completed`, `error`. (`model` / `ctx` are optional and only
rendered for Dulus.)

Bar → agent (Allow/Deny):

```json
{ "agent": "Dulus", "type": "decision", "session_id": "abc123",
  "payload": { "approved": true } }
```

---

## Platform notes

- **Windows** — fully tested. Uses `pywin32` for window focus.
- **macOS** — notch hugging + always-visible use `pyobjc` (installed automatically
  on macOS). Without pyobjc it still runs as a plain top-most island.
- **Linux** — X11 window focus via `wmctrl`/`xdotool`; on Wayland the island shows
  and stays on top, but jump-to-terminal is best-effort.

---

## Roadmap

- [ ] Pixel-perfect notch merge (squared top corners) on macOS.
- [ ] Native bridge to Dulus's ApprovalRuntime (no stdout parsing).
- [ ] Auto-detect already-running agents without a wrapper.
- [ ] Multi-monitor placement.
- [ ] Packaged installers (MSIX / notarized .dmg / AppImage).

## License

MIT — use it, fork it, ship it.
