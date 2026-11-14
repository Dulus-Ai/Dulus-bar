# Changelog

## Unreleased

Not on PyPI yet: it ships once Dulus speaks *Always allow* (see PROTOCOL.md).

### New

- **macOS: hold Allow to always allow.** Hold the button: it fills up, the
  trackpad taps once, done — a click still allows just once, and dragging off
  backs out. Right-click Allow for both spelled out; VoiceOver gets an
  "Always allow" action. Offered only when the agent can save the rule.
- **A redesigned native macOS island.** It *is* the notch (Macs without one get
  one drawn in the menu bar's empty middle) and pours open from it, its top
  corners flaring into the menu bar. Collapsed, a small light per live agent
  sits under the notch — green working, amber waiting for you, red failed.
  Open, it lists every session (agent icon and color, project, what it's doing,
  model · context, for how long) or the request waiting for you, its command
  verbatim and wrapped, never cut off; a finished agent peeks open on its own.
- **Click a session (macOS)** to bring up the terminal or editor it runs in.
- **Agents opened from the macOS menu bar icon show up on the island** (they
  run under `dulusbar wrap`), and the menu can connect Claude Code.
- **Ready for Dulus.** What an agent reports for itself while it runs under
  `dulusbar wrap` now wins over the wrapper's screen-scraping — its own
  requests (one card per prompt, not a second one read off the terminal), its
  own status — while whatever it doesn't report yet still comes from the
  wrapper. PROTOCOL.md says how an agent offers *Always allow* and keeps the
  rule in its own settings.

### Fixes

- **The native macOS island never came with `pip install`**: its Swift sources
  weren't in the package, so every pip install fell back to the Qt island. They
  ship now and are built once per version into `~/Library/Caches/Dulus Bar`
  (shared by all your installs); started from a terminal, that first build
  happens in front of you. A Mac
  without the Xcode Command Line Tools gets the Qt island straight away instead
  of an installer dialog, and a native island that dies at launch falls back
  to Qt too.
- **A double-click could answer the next request in the queue**, one you never
  saw. A new card's buttons now wake up after 350 ms (both islands), and on
  macOS an answer is bound to the card it was given on.
- **Text that hides part of a command** — a right-to-left override, a carriage
  return — is shown as `⟨U+202E⟩` on the card instead of rearranging it.
- **macOS island**: stayed at the old place after a display was plugged in or
  out; its hover area was a guess at the card's height (now measured); a hub
  restart with a card up could leave it stuck open; the first click on it could
  be lost; reconnecting kept slowing down after every hub restart.
- **Opening an agent from the macOS Qt island's menu** silently fell back to a
  fragile inline AppleScript (`os` wasn't imported), which broke on any path
  with a quote in it; an agent named after a file like `kev's-agent.py` now
  opens too.
- **Wrapping Claude Code with its hooks installed** (`dulusbar wrap "Claude Code"
  -- claude`, or *Open agent… → Claude Code*) added a second, screen-scraped
  row: running `claude` now just runs it. Without the hooks, any Claude Code
  wrapper now recognizes Claude's "Do you want to proceed?".
- Short agent aliases matched inside other words: "Example Agent" got Amp's
  icon, "Organized" Zed's. They only match whole words now.
- Model names in a dark brand color (Grok, Ollama) were unreadable on the dark
  island; they're lifted to stay legible.
- The *Always allow* hint for Claude Code's mode suggestions reads "all edits"
  instead of "mode: acceptEdits".

## 0.5.1

### Fixes

- **`dulusbar doctor > log.txt` crashed** (and any command whose output was
  redirected or piped) with `UnicodeEncodeError`: a redirected stdout on Windows
  is `cp1252`, which can't encode the `✓ ✗ ·` marks. Output now goes out as UTF-8
  with `errors="replace"`, and a diagnostic line falls back to ASCII instead of
  raising — a diagnostic must never crash.

## 0.5.0

The island grows up: first-class Claude Code support, a real protocol, and a
hub that's safe to leave running.

### New

- **Claude Code integration** — `dulusbar hooks install claude` wires Claude Code
  through its own hooks: live status (prompt, running tool, done), model and
  context size, and **Allow · Always allow · Deny** on the island. Claude's
  terminal dialog stays up alongside it; whichever you answer first wins. The
  status hooks run async, and with the island off every hook is a silent no-op.
- **Codex integration** — `dulusbar hooks install codex` points Codex's `notify` at
  the island for turn-complete alerts (never overwrites an existing `notify`).
- **`dulusbar` CLI** — `status`, `notify`, `ask` (exit 0 / 1 / 2, with a terminal
  fallback), `wrap NAME -- CMD`, `hooks install|uninstall|status`, `doctor`, `quit`.
- **Permission queue** — simultaneous requests line up ("1 of 3") instead of
  overwriting each other, each card showing the actual command, diff or URL.
- **Desktop notifications** (Windows / Linux) when an agent needs approval,
  finishes or fails; the island now peeks open only on those, not on every tool call.
- **Settings** in the menu — auto-hide, peek on finish, notifications, do not
  disturb, **start at login** (XDG autostart / LaunchAgent / Run key) — saved
  to `config.json`.
- **Integrations menu** to connect / disconnect Claude Code and Codex from the island.
- **Click-to-focus by process** (Windows / Linux island) — clicking a session
  raises the terminal or editor that actually hosts it (walks up from the agent's pid).
- **`dulus_bar.client`** — a dependency-free client SDK (one vendorable file) and
  [PROTOCOL.md](PROTOCOL.md), the full v2 wire format.

### Protocol v2 (backwards compatible)

- Optional `hello` with roles: `agent` (no auth, v1 default) and `ui` (needs the
  per-run token from `hub.json`). UIs get a `snapshot` on connect.
- `request_id` on requests; decisions go only to the agent that asked.
- `request_resolved` when a request is settled anywhere (answered in the
  terminal, withdrawn, requester gone) so no stale card is left behind.
- `ts` ordering for short-lived hook processes; new events `prompt`,
  `tool_started`, `tool_finished`, `idle`, `session_ended`.
- The hub cleans up sessions whose process died, and never prunes one it knows
  is alive.
- No keepalive pings on localhost (the OS reports a dead peer at once), so an
  idle SDK client stays connected; `BarClient` also reconnects once after a hub
  restart.

### Security

- Browser origins are refused: a web page could previously open
  `ws://127.0.0.1:17372`, read every agent's tool calls and approve them.
- Only an authenticated island may answer a request, and agents no longer
  receive each other's traffic.
- The permission card rendered agent-supplied text as rich text, so a command
  could hide part of itself (`true <font color=…>; curl … | sh;</font> ls` showed
  as `true ; ls`) and still get approved. Commands are now always shown verbatim,
  wrapped instead of cut off.
- Installing the Claude Code / Codex integrations keeps the config file's
  permissions and writes through symlinks, instead of replacing a `0600`
  `settings.json` with a world-readable copy.

### Fixes

- **Quit Dulus Bar** didn't quit: Qt cancels `quit()` when a window refuses to
  close, and the island hides instead of closing.
- Wrappers re-reported the same prompt on every TUI redraw, stacking duplicate
  cards — and each extra "Allow" typed a stray `Y` into the agent. One prompt is
  now one request, answers are only typed while a prompt is pending, and typing
  in the terminal withdraws the card.
- Wrappers hung after the agent exited (and its session lingered as "running").
- Wrapper answers under a PTY: "Allow" presses Enter (`\r`), which raw-mode TUIs
  expect; "Deny" never does, because in a select list Enter picks the default
  (usually "Yes"). Claude Code, Codex, Gemini and Qwen get Enter / Esc, and Codex's
  "Would you like to run the following command?" is recognized.
- Focus, mouse and query replies the terminal sends on its own no longer count as
  "answered in the terminal".
- macOS native island: a disconnected session came back as "running"; a second
  request overwrote the first; `completed` ignored the message text. It also
  rebuilds itself when its sources are newer than the binary.
- Panel rows are updated in place instead of rebuilt every second (no flicker),
  long text is elided to the real pixel width, the pill is never clipped when
  expanded, and agent emoji sit centred in the pill.
- The island never takes keyboard focus, so a request popping up can't swallow
  what you're typing.
- PyInstaller builds include the brand icon; frozen builds launch agents and run
  hooks by re-invoking themselves (`DulusBar wrap …`, `DulusBar hook claude`).
- Docs link points at the GitHub repo.
