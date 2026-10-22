# Dulus Bar protocol (v2)

Everything an agent — Interant, a hook, a script, your own CLI — needs to
report to the island and get Allow / Deny back. No account, no cloud: one
WebSocket on `ws://127.0.0.1:17372`.

> **The quickest route is the SDK.** [`dulus_bar/client.py`](dulus_bar/client.py)
> is a single, standard-library-only file (vendor it as-is) that speaks
> everything below:
>
> ```python
> from dulus_bar.client import BarClient
>
> with BarClient(agent="Dulus", session_id="abc123") as bar:
>     bar.event("session_started", {"model": "kimi-k2.5", "ctx": "12%", "cwd": os.getcwd()})
>     decision = bar.request_permission("Bash", "rm -rf build/", detail="rm -rf build/")
>     if decision and decision.approved:
>         ...  # decision.always: the user picked "Always allow"
> ```

v2 is a strict superset of v1: a v1 client keeps working unchanged, and a v2
client degrades gracefully against a v1 hub.

## Connecting

| | |
| --- | --- |
| URL | `ws://127.0.0.1:17372` (`$DULUS_BAR_PORT` changes the port; `$DULUS_BAR_SERVER` overrides the whole URL) |
| Discovery | the running hub writes `hub.json` (mode `0600`) to the app dir: `{"port", "token", "pid", "version"}` |
| App dir | Linux `~/.config/dulus-bar` · macOS `~/Library/Application Support/Dulus Bar` · Windows `%APPDATA%\Dulus Bar` (`$DULUS_BAR_HOME` overrides) |
| Origin | connections that carry a browser `Origin` header are refused (HTTP 403). Native clients send none. |

### `hello` (optional)

The first message may identify the client:

```json
{"type": "hello", "v": 2, "role": "agent", "ephemeral": true, "client": "my-agent/1.0",
 "agent": "_health", "session_id": "_health"}
```

* `role` — `agent` (default) or `ui`. **`ui` requires `"token"`** from `hub.json`;
  without it the hub answers `{"type": "error", "error": "unauthorized"}` and closes.
* `ephemeral` — `true` for clients that connect once per event (hooks, scripts).
  When a non-ephemeral agent socket closes, the hub ends the sessions it reported
  (`disconnected`); an ephemeral one can come and go freely.
* `agent: "_health"` makes a v1 hub treat the hello as a health probe instead
  of a session called "unknown".

The hub answers `{"type": "welcome", "v": 2, "server": "dulus-bar/0.5.0", "role": …, "authed": …}`.
Clients without a hello are treated as long-lived `agent`s (v1 behaviour).

## Agent → island

Every event has the same envelope:

```json
{"agent": "Claude Code", "type": "tool_started", "session_id": "abc123",
 "payload": {"tool": "Bash", "args": "npm test"}, "ts": 1792632500.0}
```

* `agent` — display name. Known names (Claude, Codex, Gemini, Cursor, Dulus, …)
  get their icon and accent color.
* `session_id` — anything stable per session.
* `ts` *(v2, optional)* — when it happened (epoch seconds). Events older than
  the newest one already applied to that session are ignored, so short-lived
  hook processes can't reorder a session's state. Permission requests are
  never dropped.

| `type` | Session becomes | Payload |
| --- | --- | --- |
| `session_started` | running | `text`? |
| `prompt` *(v2)* | running — the user submitted a prompt | `text` (the prompt) |
| `tool_started` *(v2)* | running | `tool`, `args` |
| `tool_finished` *(v2)* | running | `tool`, `args` |
| `message` | running | `text` |
| `tool_request` | waiting — see below | `tool`, `args`, `detail`?, `can_always`?, `always_label`? |
| `tool_approved` / `tool_denied` | running | — |
| `idle` *(v2)* | done — waiting for the user | `text`? |
| `completed` | done | `text`? |
| `error` | error | `text` |
| `session_ended` *(v2)* | removed | `reason`? |

Optional metadata on **any** event, shown next to the agent name:

| key | example | shown as |
| --- | --- | --- |
| `model` | `"opus 4.5"`, `"kimi-code/kimi-k3"` | accent-colored model name |
| `ctx` | `"38%"` or `"82k"` | `ctx 38%` (percentages are colored green → amber → red) |
| `cwd` | `"/home/kev/api"` | the folder name, as the session title |
| `title` | `"api · staging"` | the session title (wins over `cwd`) |
| `pid` + `pid_host` | `4242`, `"linux:my-host"` | the hub removes the session when that process dies; clicking the row raises the terminal hosting it |
| `terminal_hint` | `"Codex"` | window title used for click-to-focus |

`pid_host` is `f"{sys.platform}:{socket.gethostname()}"`; the hub ignores pids
from a different host (WSL is not Windows).

**Running under a wrapper?** `dulusbar wrap` exports `DULUS_BAR_AGENT` and
`DULUS_BAR_SESSION_ID` to the agent it launches. Report with that agent name
and session id (`BarClient()` picks them up by default) and your events — exact
model, context, tool calls — land in the wrapper's row instead of a second one.

### Permission requests

```json
{"agent": "Claude Code", "type": "tool_request", "session_id": "abc123",
 "request_id": "9f2c1d0a7b3e",
 "payload": {"tool": "Bash", "args": "rm -rf build/",
             "detail": "rm -rf build/\n# Clean and rebuild",
             "can_always": true, "always_label": "Bash(rm:*)"}}
```

* `request_id` *(v2)* — any unique string. Omitted by a v1 client, the hub
  assigns one; a v1 client re-sending the same prompt while it's still pending
  is ignored instead of stacking a second card.
* `tool` / `args` — one line each: what the card's title and the session row say.
* `detail` — the multi-line version (the full command, a diff excerpt).
* `can_always` — offer **Always allow**; `always_label` says what it allows.

Requests queue first-in, first-out across every agent ("1 of 3" on the card).

The answer goes **only to the socket that asked**:

```json
{"type": "decision", "agent": "Claude Code", "session_id": "abc123", "request_id": "9f2c1d0a7b3e",
 "payload": {"approved": true, "always": false, "message": ""}}
```

v1 clients that ignore `request_id` and match on `session_id` keep working.

A request can also end without a decision. Islands then get
`{"type": "request_resolved", "request_id": …, "payload": {"outcome": …}}` and
drop the card; so does the asker, if it's still connected, and it should stop
waiting. That happens when:

* the asker sent `{"type": "cancel", "request_id": …}` (e.g. the user answered in the terminal);
* the asker's socket closed;
* the session moved on: `tool_finished` with the same `tool`/`args`, `prompt`,
  `idle`, `completed`, or `tool_approved` / `tool_denied`. That means the
  prompt was answered somewhere else — usually right in the terminal.

## Island (UI) → hub

Only an authenticated `ui` peer may answer:

```json
{"type": "decision", "request_id": "9f2c1d0a7b3e", "payload": {"approved": true, "always": false}}
```

A `ui` peer receives, right after `welcome`:

```json
{"type": "snapshot", "v": 2, "sessions": [{…}], "permissions": [{…}]}
```

…then every agent event (with `request_id` / `ts` filled in), plus hub events:

| `type` | meaning |
| --- | --- |
| `request_resolved` | a request is settled: `payload.outcome` is `approved`, `denied`, `cancelled` or `answered`, and `payload.by` is `ui`, `agent` or `hub` |
| `disconnected` | a long-lived agent socket closed: drop its session |
| `session_removed` | the session was dismissed, its process died, or — with no socket or pid to tell it's alive — it went silent for 30 minutes |

Agents never receive other agents' traffic.

## Control (needs the token)

| send | get |
| --- | --- |
| `{"type": "status", "token": "…"}` | `{"type": "status", "server": {"version", "pid", "port", …}, "sessions": […], "permissions": […]}` |
| `{"type": "shutdown", "token": "…"}` | `{"type": "shutdown", "ok": true}`, then the island quits |

## Health probes

Messages with `type` `ping` / `health` / `pong`, or `agent` `_health` /
`DulusHealth` / `VibeHealth`, are ignored: they never become sessions and are
never relayed. Use them to check that the hub is alive.
