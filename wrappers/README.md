# Wrappers

Wrappers launch the real agent and report its status live to Dulus Bar
(`ws://127.0.0.1:17372`).

## How they work

1. Open a websocket to the Dulus Bar event server (with retry).
2. Run the real agent binary/script with piped stdio.
3. Forward your terminal input to the agent.
4. Forward the agent's stdout/stderr to your terminal.
5. Detect permission prompts and send events to the bar.
6. Listen for Allow/Deny decisions from the bar and inject them into the agent's stdin.
7. If the bar isn't up, the agent **still runs** (just without the live feed).

For Dulus, the wrapper also reports the active **model** and **context usage**,
which the bar renders next to the Dulus session (other agents just show status).

## Included wrappers

| Script | Agent |
|--------|-------|
| `dulus_wrapper.py` | **Dulus** (local `dulus.py`) — reports model + ctx |
| `claude_wrapper.py` | Claude Code |
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
python wrappers/dulus_wrapper.py
python wrappers/dulus_wrapper.py "fix the login"
python wrappers/dulus_wrapper.py -m kimi/kimi-k2.5
python wrappers/dulus_wrapper.py --dulus "/path/to/dulus.py"
```

The path is remembered in `../dulus_path.txt` the first time it resolves.

## Any other agent

```bash
python wrappers/claude_wrapper.py fix auth bug
python wrappers/agent_wrapper.py "Codex" codex fix auth bug
python wrappers/agent_wrapper.py "Gemini" gemini fix auth bug
```

`agent_wrapper.py "Display Name" <command> [args...]` works for *any* CLI agent.
