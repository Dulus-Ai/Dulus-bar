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

- **All platforms** — Windows, macOS, and Linux.
- **macOS notch-native** — on MacBooks with a camera notch the island hugs the
  notch and stays visible across every Space and full-screen app.
- **Reveal on hover** — everywhere else it rests as a slim tab at the top edge and
  slides open when your mouse approaches, or when an agent needs you.
- **Any agent** — anything with a CLI works through a thin wrapper; well-known
  agents get their own icon and accent color.
- **100% local** — no cloud, no account, no telemetry. One WebSocket on
  `127.0.0.1:17372`.

---

## Install

Requires Python 3.10+.

```bash
pip install dulus-bar
dulusbar
```

That's it — the island appears at the top of your screen. Move your mouse to the
top-center to reveal it; right-click the tray icon for the menu.

<details>
<summary><strong>From source (for the wired-agent extras)</strong></summary>

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
> Swift toolchain in Xcode Command Line Tools. The Python process stays the local
> WebSocket hub; Windows and Linux use the PyQt overlay.

</details>

---

## Wire up any AI agent — zero monopoly

Dulus Bar is **agent-agnostic**. Wrap any CLI and it reports to the island:

```bash
python wrappers/agent_wrapper.py "Codex" codex fix auth bug
python wrappers/agent_wrapper.py "Gemini" gemini
python wrappers/claude_wrapper.py            # Claude Code helper
```

`agent_wrapper.py "Display Name" <command> [args...]` reports that agent to the
bar. Known names (Claude, Codex, Gemini, Cursor, Copilot, Kimi, DeepSeek, Grok,
Qwen, Mistral, Windsurf, Zed, Aider, Ollama, …) get a curated icon + color;
anything else gets a stable auto-assigned color.

Or **right-click the island → Open agent…** and pick any executable from a file
dialog — Dulus Bar launches it in its own terminal, already wired.

---

## Works beautifully with Dulus (optional)

If you run [Dulus](https://dulus.ai), the island also shows the active **model**
and live **context usage** — e.g. `kimi/kimi-k2.5 · ctx 38%`. But you never need
Dulus to use Dulus Bar. The bar is the gift; Dulus is just one of many agents it
speaks to.

---

## What it does

- Floating island, top-center (or notch-anchored on macOS).
- Lists active agents with a live status dot (running / waiting / done / error).
- When an agent asks for permission, shows **Allow / Deny** right on the island and
  forwards your choice back to the agent over stdin.
- Reveals on hover, pops open on agent activity, and **stays open on a permission
  request** until you answer.
- Click an agent to jump to its terminal / editor window.

---

## How it works

Agents connect to a tiny local WebSocket hub and send status; the island renders
it and relays your Allow/Deny back.

**Agent → bar:**
```json
{ "agent": "Claude", "type": "tool_request", "session_id": "abc123",
  "payload": { "tool": "Bash", "args": "rm -rf build/" } }
```
Event types: `session_started`, `message`, `tool_request`, `tool_approved`,
`tool_denied`, `completed`, `error`. (`model` / `ctx` payload fields are optional.)

**Bar → agent (Allow/Deny):**
```json
{ "type": "decision", "session_id": "abc123", "payload": { "approved": true } }
```

```
dulus_bar/
  overlay.py        Floating island UI (PyQt6), notch-aware + reveal-on-hover
  server.py         WebSocket hub (ws://127.0.0.1:17372)
  agents.py         Per-agent icon + accent color registry
  native/           win32 / pyobjc / wmctrl window focus per platform
wrappers/
  base_wrapper.py   Shared: pipe stdio, detect prompts, inject Allow/Deny
  agent_wrapper.py  Generic wrapper for any CLI agent
macos/              Native SwiftUI/AppKit notch surface
```

---

## Build a standalone binary (optional)

```bash
pip install pyinstaller
pyinstaller --noconfirm DulusBar.spec
```

Output lands in `dist/` — `DulusBar.exe` on Windows, `DulusBar.app` on macOS
(bundled as an `LSUIElement`, so no Dock icon).

---

## Platform notes

- **Windows** — fully tested. `pywin32` for window focus; PyQt overlay with
  reveal-on-hover.
- **macOS** — notch hugging + always-visible via `pyobjc`. Without it, still runs
  as a plain top-most island.
- **Linux** — X11 window focus via `wmctrl`/`xdotool`; on Wayland the island shows
  and stays on top, jump-to-terminal is best-effort.

---

## Contributing

PRs welcome — new agent icons/colors, platform fixes, packaging. Open an issue,
fork it, ship it.

## License

**MIT.** Use it, fork it, ship it. We shipped the idea on purpose — no monopoly.

<p align="center"><sub>Built by one builder in the Dominican Republic 🇩🇴 — for everyone. 🦅</sub></p>
