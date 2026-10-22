<h1 align="center">Dulus Bar 🦅</h1>

<p align="center">
  <strong>A cross-platform Dynamic Island for your AI agents.</strong><br>
  Free. Native. MIT. Works with <em>any</em> agent — no account, no cloud, no lock-in.
</p>

<p align="center">
  <code>pip install dulus-bar</code>
</p>

<p align="center">
  <img alt="MIT" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="Platforms" src="https://img.shields.io/badge/platform-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-lightgrey">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Dulus-Ai/Dulus-bar/main/assets/island.png" alt="Dulus Bar — the floating island with a live agent and an Allow/Deny prompt" width="78%">
  <br><sub><em>Live model + context per agent, and Allow / Deny right on the island — no diving back into a terminal.</em></sub>
</p>

---

Dulus Bar puts a floating status island at the top of your screen that shows every
AI coding agent you're running — Claude Code, Codex, Gemini, Cursor, Dulus, and
any other CLI — and lets you **Allow / Deny** their permission prompts without
diving back into a terminal.

It's the idea itself, shipped for everyone. **Not tied to Dulus** — wrap any agent
and it shows up. We built the island we wanted; here it is for the whole world. 🌎

- **Claude Code, wired in one command** — `dulusbar hooks install` connects it
  through Claude Code's own hooks: live status, model and context size, and
  **Allow · Always allow · Deny** right on the island, alongside the terminal
  dialog (answer whichever you see first).
- **Any agent** — `dulusbar wrap Codex -- codex` wires up anything with a CLI;
  well-known agents get their own icon and accent color.
- **Any script** — `dulusbar notify "Deploy done"` and
  `dulusbar ask "Run migrations?" && ./migrate.sh` put your own tools on the island.
- **Every request, in order** — several agents asking at once queue up ("1 of 3"),
  each with the actual command or diff to approve.
- **All platforms** — Windows, macOS (notch-native) and Linux.
- **Quiet by design** — it rests as a slim tab at the top edge (or in the notch),
  peeks open when an agent finishes or fails, and stays open while something needs
  you. On Windows and Linux it also sends a desktop notification, so you can look away.
- **100% local and locked down** — one WebSocket on `127.0.0.1:17372`. Web pages
  can't reach it, and only the island itself can approve anything. No cloud, no
  account, no telemetry.

---

## Install

Requires Python 3.10+.

```bash
pip install dulus-bar
dulusbar                     # start the island
dulusbar hooks install       # connect Claude Code (and Codex, if you have it)
```

That's it — the island appears at the top of your screen. Move your mouse to the
top-center to reveal it; right-click it (or the tray icon) for the menu, where
**Integrations** and **Settings → Start at login** live too.

Something off? `dulusbar doctor` checks everything and tells you how to fix it.

<details>
<summary><strong>From source</strong></summary>

```bash
git clone https://github.com/Dulus-Ai/Dulus-bar.git
cd Dulus-bar

# macOS / Linux
./setup.sh

# Windows
.\setup.ps1
```

> Linux tip: for click-to-focus of a terminal on X11, install `wmctrl` or `xdotool`.
>
> macOS: `setup.sh` also builds the native SwiftUI/AppKit notch surface using the
> Swift toolchain in Xcode Command Line Tools (and it rebuilds itself after a
> `git pull`). The Python process stays the local WebSocket hub; Windows and Linux
> use the PySide6 overlay.

</details>

---

## Claude Code

```bash
dulusbar hooks install claude
```

This adds Dulus Bar's hooks to `~/.claude/settings.json` (every other hook you have
is left alone, and the file is backed up first). Restart your Claude Code sessions
and each one shows up on its own — no wrapper needed:

- **Live status** — the prompt it's working on, the tool it's running, "done" when
  it hands the turn back, plus the **model** and **context size** (`opus 4.5 · ctx 82k`).
- **Allow · Always allow · Deny** — every permission prompt appears on the island
  with the exact command, file or URL. Claude Code still shows its own dialog in
  the terminal; whichever you answer first wins, and the other one goes away.
  *Always allow* applies the same "don't ask again" rule Claude would offer you.
- **Zero drag** — the status hooks run in the background, and when the island
  isn't running every hook exits instantly without output: Claude Code behaves
  exactly as if Dulus Bar weren't installed.
- In the session list (Windows / Linux), click a session to jump to the terminal
  or editor it runs in.

`dulusbar hooks status` shows what's connected; `dulusbar hooks uninstall claude`
removes our hooks and only ours.

## Codex

```bash
dulusbar hooks install codex
```

Points Codex's `notify` at the island, so you get a "done" with Codex's last message
the moment it hands the turn back. If your `~/.codex/config.toml` already has a
`notify` program, Dulus Bar won't overwrite it. For live status and Allow / Deny,
wrap it instead: `dulusbar wrap Codex -- codex`.

## Any agent — zero monopoly

Dulus Bar is **agent-agnostic**. Wrap any CLI and it reports to the island:

```bash
dulusbar wrap Codex -- codex "fix auth bug"
dulusbar wrap Gemini -- gemini
dulusbar wrap "My Agent" -- ./my-agent --flag
```

The wrapper runs the agent in a real PTY (so interactive TUIs work), streams its
status, and types your Allow / Deny into it. Known names (Claude, Codex, Gemini,
Cursor, Copilot, Kimi, DeepSeek, Grok, Qwen, Mistral, Windsurf, Zed, Aider, Ollama, …)
get a curated icon + color; anything else gets a stable auto-assigned color.

Or **right-click the island → Open agent…** and pick an installed agent or any
executable — Dulus Bar launches it in its own terminal, already wired.

## Your scripts, on the island

```bash
# Post a status (exit code is always 0, so it never breaks your script)
make release && dulusbar notify --agent CI "Release built ✓"
dulusbar notify --status error --agent CI "Tests failed"

# Ask before doing something risky: 0 = allowed, 1 = denied, 2 = no answer
dulusbar ask --detail "alembic upgrade head" "Run migrations on prod?" && ./migrate.sh
```

`ask` falls back to a `[y/N]` prompt in the terminal when the island isn't running.

## Build your own integration

Agents talk to the island over one local WebSocket. [`dulus_bar/client.py`](dulus_bar/client.py)
is a dependency-free client you can import or vendor as a single file:

```python
from dulus_bar.client import BarClient

with BarClient(agent="My Agent") as bar:
    bar.event("session_started", {"model": "kimi-k2.5", "ctx": "12%"})
    decision = bar.request_permission("Bash", "rm -rf build/")
    if decision and decision.approved:
        ...
```

The full wire format — events, permission requests, decisions, auth — is in
[PROTOCOL.md](PROTOCOL.md).

## Works beautifully with Dulus (optional)

If you run [Dulus](https://dulus.ai), the island also shows the active **model**
and live **context usage** — e.g. `kimi/kimi-k2.5 · ctx 38%`. But you never need
Dulus to use Dulus Bar. The bar is the gift; Dulus is just one of many agents it
speaks to.

---

## Commands

| Command | What it does |
| --- | --- |
| `dulusbar` | Start the island (frees your terminal; `--foreground` keeps the logs there) |
| `dulusbar status [--json]` | What every agent is doing, and what's waiting for approval |
| `dulusbar notify [-a NAME] [--status running\|done\|error\|idle] MESSAGE` | Post a status from any script |
| `dulusbar ask [--detail TEXT] [--timeout S] QUESTION` | Allow / Deny on the island; exit 0 / 1 / 2 |
| `dulusbar wrap NAME -- COMMAND [ARGS…]` | Run any CLI agent wired to the island |
| `dulusbar hooks install\|uninstall\|status [claude\|codex\|all]` | Connect Claude Code / Codex |
| `dulusbar doctor` | Diagnose the setup |
| `dulusbar quit` | Stop the running island |

## Security

- The hub listens on `127.0.0.1` only.
- Any web page can try to open a WebSocket to `localhost`, so connections that carry
  a browser `Origin` are refused: a website can't read your agents' tool calls or
  approve one.
- Only the island can answer a permission request — it authenticates with a token
  that changes every run and lives in a file only you can read (`hub.json`, mode `0600`).
- An answer is delivered only to the agent that asked.

---

## How it works

Agents (hooks, wrappers, scripts) connect to a tiny local WebSocket hub and report
events; the hub keeps the state, the island renders it, and your Allow / Deny goes
back to exactly the agent that asked.

**Agent → bar:**
```json
{ "agent": "Claude Code", "type": "tool_request", "session_id": "abc123", "request_id": "r1",
  "payload": { "tool": "Bash", "args": "rm -rf build/" } }
```

**Bar → that agent:**
```json
{ "type": "decision", "session_id": "abc123", "request_id": "r1", "payload": { "approved": true } }
```

Event types: `session_started`, `prompt`, `tool_started`, `tool_finished`, `message`,
`tool_request`, `tool_approved`, `tool_denied`, `idle`, `completed`, `error`,
`session_ended`. `model` / `ctx` / `cwd` payload fields are optional. Clients that
speak the original v1 protocol keep working unchanged — see [PROTOCOL.md](PROTOCOL.md).

```
dulus_bar/
  overlay.py        Floating island UI (PySide6), notch-aware + reveal-on-hover
  server.py         WebSocket hub (ws://127.0.0.1:17372): roles, routing, housekeeping
  state.py          Session + permission state, shared by the hub and the island
  client.py         Zero-dependency client SDK (hooks, CLI, your agent)
  cli.py            The `dulusbar` command
  integrations/     Claude Code hooks, Codex notify
  agents.py         Per-agent icon + accent color registry
  native/           win32 / pyobjc / wmctrl window focus per platform
  wrappers/         PTY / pipe wrapper for any CLI agent
macos/              Native SwiftUI/AppKit notch surface
```

---

## Build a standalone binary (optional)

```bash
pip install pyinstaller
pyinstaller --noconfirm DulusBar.spec
```

Output lands in `dist/` — `DulusBar.exe` on Windows, `DulusBar.app` on macOS
(bundled as an `LSUIElement`, so no Dock icon). The binary is the CLI too
(`DulusBar hooks install`, `DulusBar status`, …): hooks and launched agents
re-invoke it, so no separate Python install is needed.

---

## Platform notes

- **Windows** — fully tested. `pywin32` for window focus; PySide6 overlay with
  reveal-on-hover.
- **macOS** — notch hugging + always-visible via the native SwiftUI surface (or
  `pyobjc` in the Qt fallback). Without either, still runs as a plain top-most island.
- **Linux** — anchors to the top of the *work area* (never under a panel/dock)
  and takes itself out of the window manager's placement policy on X11, so it
  stays at the top instead of being re-centred. On Wayland it runs through
  XWayland, because a Wayland client cannot position its own window. Agents open
  in your real terminal: `gnome-terminal`, `ptyxis`, `kgx`, `konsole`,
  `xfce4-terminal`, `tilix`, `terminator`, `lxterminal`, `alacritty`, `kitty`,
  `foot`, `wezterm`, `ghostty`, `qterminal`, `urxvt`, `st` and `xterm` are each
  launched with the flags they actually support, and `$TERMINAL` wins if set.
  Install `wmctrl` or `xdotool` for click-to-focus on X11. Without a system tray
  (stock GNOME) the island stays visible instead of hiding — right-click it for
  the menu.

### Knobs

Most settings live in the menu (**Settings**). Environment variables still win:

| Variable | Effect |
| --- | --- |
| `DULUS_BAR_PORT=17372` | Port of the local hub (agents follow it automatically). |
| `DULUS_BAR_HOME=<dir>` | Where settings and `hub.json` live. |
| `DULUS_BAR_NO_AUTOHIDE=1` | Keep the island always visible (no reveal-on-hover). |
| `DULUS_BAR_FOREGROUND=1` | Run in the foreground and keep the logs in the terminal. |
| `DULUS_BAR_ALLOWED_ORIGINS=<a,b>` | Let specific web origins connect (e.g. your own dashboard). |
| `DULUS_BAR_DEBUG=1` | Log hook errors to `hooks.log` in the settings dir. |
| `DULUS_BAR_FORCE_QT=1` | macOS: use the Qt island instead of the native one. |
| `DULUS_BAR_X11_WM=1` | Linux: let the window manager manage/place the island again. |
| `DULUS_BAR_KEEP_WAYLAND=1` | Linux: stay on native Wayland (the compositor decides where the island goes). |
| `DULUS_BAR_FULL_SCREEN_EDGE=1` | Linux: anchor to the physical screen edge, ignoring panels/docks. |
| `DULUS_BAR_OPAQUE=1` | Drop translucency + shadows for window managers with no compositor. |

---

## Contributing

PRs welcome — new agent icons/colors, integrations, platform fixes, packaging.
`pip install -e ".[dev]" && pytest` runs the suite (headless; the UI tests use
Qt's offscreen platform). Open an issue, fork it, ship it.

## License

**MIT.** Use it, fork it, ship it. We shipped the idea on purpose — no monopoly.

<p align="center"><sub>Built by one builder in the Dominican Republic 🇩🇴 — for everyone. 🦅</sub></p>
