# Wrappers

Wrappers launch the real agent and report its status live to Dulus Bar
(`ws://127.0.0.1:17372`). The easy way to use them is the CLI:

```bash
dulusbar wrap Codex -- codex "fix auth bug"
dulusbar wrap Gemini -- gemini
```

> **Claude Code doesn't need a wrapper**: `dulusbar hooks install claude` wires it
> through its own hooks (status, model, context and Allow / Deny). With the hooks
> installed, `claude_wrapper.py` simply runs `claude`.

## How they work

1. Open a websocket to the Dulus Bar event server (with retry).
2. Run the real agent under a PTY when there's a terminal (so interactive TUIs
   work), with piped stdio otherwise (Windows).
3. Forward your terminal input to the agent, and its output to your terminal.
4. Detect permission prompts and report each one **once** — a TUI repainting
   its prompt doesn't stack duplicate cards.
5. Type the island's Allow / Deny into the agent — only while a prompt is
   pending. Answer in the terminal instead and the island's card is withdrawn.
6. If the bar isn't up, the agent **still runs** (just without the live feed).

For Dulus, the wrapper also reports the active **model** and **context usage**,
which the bar renders next to the Dulus session (other agents just show status).

## Included wrappers

| Script | Agent |
|--------|-------|
| `dulus_wrapper.py` | **Dulus** (local `dulus.py`) — reports model + ctx |
| `claude_wrapper.py` | Claude Code (defers to the hooks when they're installed) |
| `agent_wrapper.py` | Any CLI (Cursor, Codex, Gemini, Kimi, …) |
| `base_wrapper.py` | Shared base |
| `paths.py` | Auto-resolve of `dulus.py` |

## Dulus (the easy path)

```bash
# from the repo root
./dulusbar                 # macOS / Linux
```

```powershell
.\connect.cmd              # Windows (double-click works too)
# or, after setup.ps1:
dulusbar
dulus-connect
```

Manual:

```bash
python dulus_bar/wrappers/dulus_wrapper.py
python dulus_bar/wrappers/dulus_wrapper.py "fix the login"
python dulus_bar/wrappers/dulus_wrapper.py -m kimi/kimi-k2.5
python dulus_bar/wrappers/dulus_wrapper.py --dulus "/path/to/dulus.py"
```

The path is remembered in `../dulus_path.txt` the first time it resolves.

## Any other agent

```bash
dulusbar wrap "Cursor Agent" -- cursor-agent
python dulus_bar/wrappers/agent_wrapper.py "Codex" codex fix auth bug   # same thing
```

`agent_wrapper.py "Display Name" <command> [args...]` works for *any* CLI agent.
To speak to the island natively instead (no wrapper), see `PROTOCOL.md` and
`dulus_bar/client.py` at the repo root.
