"""Base wrapper for any CLI agent. Launch the agent through this to pipe events to Dulus Bar.

Usage:
    python base_wrapper.py "Agent Name" /path/to/agent-bin [agent args...]
    dulusbar wrap "Agent Name" -- /path/to/agent-bin [agent args...]

The wrapper:
  - Opens websocket to ws://127.0.0.1:17372.
  - Spawns the real agent (under a PTY when there's a terminal, else pipes).
  - Forwards your terminal input to the agent, and its output to your terminal.
  - Sends status/permission events to the island.
  - Listens for Allow/Deny decisions from the island and types them into the agent.

One prompt is reported once: while it's pending nothing else is sent for it,
and if you answer in the terminal instead, the island's card is withdrawn.

Optional `model` / `ctx` metadata (surfaced by Dulus Bar for Dulus): pass a
starting model to the constructor and/or populate `model_patterns` / `ctx_patterns`
to scrape them live from the agent's stdout.
"""

import asyncio
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from typing import List, Optional

import websockets


# New name first, old name kept as a fallback for existing shells.
EVENT_SERVER = (
    os.getenv("DULUS_BAR_SERVER")
    or os.getenv("VIBE_ISLAND_SERVER")
    or f"ws://127.0.0.1:{os.getenv('DULUS_BAR_PORT') or 17372}"
)

# Status lines are coalesced to at most one event per interval: TUIs redraw
# constantly and every redraw used to become an event.
MESSAGE_INTERVAL = 0.3
# After a prompt is answered the TUI often redraws it once more before it
# processes the key; don't mistake that echo for a new prompt.
PROMPT_GRACE = 1.5


# CSI / OSC / single-char escape sequences — stripped before pattern matching
# so PTY output (which is full of ANSI) still matches permission/done regexes.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"      # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[@-Z\\-_]"                  # 2-char escapes
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# Input the *terminal* sends on its own — focus in/out, mouse reports, replies
# to cursor / device queries. It must not count as the user answering a prompt
# (alt-tabbing away used to withdraw the island's card).
_TERMINAL_INPUT_RE = re.compile(
    rb"\x1b\[[IO]"                       # focus in / out
    rb"|\x1b\[M[\s\S]{3}"                 # X10 mouse report
    rb"|\x1b\[<[\d;]*[Mm]"                # SGR mouse report
    rb"|\x1b\[\??[\d;]*[Rcn]"              # cursor position / device attributes / status
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC replies (colors, titles)
)

# Agents whose approval prompt is a select list: Enter takes the highlighted
# "Yes", Esc is "No". (Typing "n" + Enter there would pick the default: allow.)
_MENU_AGENTS = ("claude", "codex", "gemini", "qwen")


def is_user_input(data: bytes) -> bool:
    return bool(_TERMINAL_INPUT_RE.sub(b"", data))


class AgentWrapper:
    def __init__(self, agent_name: str, command: List[str], model: str = ""):
        self.agent_name = agent_name
        self.command = command
        self.model = model
        self.ctx = ""
        # Optional regexes to scrape live model/ctx from stdout (agent-specific).
        self.model_patterns: List[re.Pattern] = []
        self.ctx_patterns: List[re.Pattern] = []
        self.session_id = str(uuid.uuid4())[:8]
        self.permission_patterns = [
            re.compile(r"Allow\s+.*?\?", re.I),
            re.compile(r"Claude\s+would\s+like\s+to\s+(.*?\.)", re.I | re.S),
            re.compile(r"Do\s+you\s+want\s+to\s+allow\s+(.*?)\?", re.I | re.S),
            re.compile(r"\(Y/n\)", re.I),
            re.compile(r"\(y/N\)", re.I),
            # Codex: "Would you like to run the following command?"
            re.compile(r"Would\s+you\s+like\s+to\s+(?:run\s+the\s+following\s+command"
                       r"|make\s+the\s+following\s+edits|grant\s+these\s+permissions)", re.I),
        ]
        self.done_patterns = [
            re.compile(r"✓\s+Done"),
        ]
        # Report permission prompts at all (off when the agent has real hooks).
        self.detect_permissions = True
        # What the island's answer types into the agent. Under a PTY, "\r" is
        # Enter for raw-mode TUIs (and becomes "\n" in cooked mode); pipes get
        # a plain newline. A "No" never presses Enter under a PTY: in a select
        # list, Enter picks the highlighted default — usually "Yes".
        self.answer_yes_pty = "Y\r"
        self.answer_no_pty = "n"
        self.answer_yes_pipe = "Y\n"
        self.answer_no_pipe = "n\n"
        if any(name in agent_name.lower() for name in _MENU_AGENTS):
            self.answer_yes_pty, self.answer_no_pty = "\r", "\x1b"
        self._agent_stdin: Optional[asyncio.StreamWriter] = None
        self._pty_master: Optional[int] = None  # set when running under a PTY
        self._ws = None
        self._pending_request: Optional[str] = None
        self._quiet_until = 0.0
        self._scan = ""  # recent ANSI-free output, scanned for prompts
        self._last_prompt_id = ""
        self._last_message = ""
        self._last_message_at = 0.0
        self._flush_handle: Optional[asyncio.TimerHandle] = None

    def make_event(self, event_type: str, payload: dict, request_id: str = "") -> dict:
        event = {
            "agent": self.agent_name,
            "type": event_type,
            "session_id": self.session_id,
            "payload": payload,
            "ts": time.time(),
        }
        if request_id:
            event["request_id"] = request_id
        return event

    def _meta_payload(self) -> dict:
        """model / ctx fields Dulus Bar shows for Dulus (empty for others)."""
        meta: dict = {}
        if self.model:
            meta["model"] = self.model
        if self.ctx:
            meta["ctx"] = self.ctx
        return meta

    def _scan_meta(self, buffer: str) -> None:
        for pat in self.model_patterns:
            m = pat.search(buffer)
            if m:
                self.model = (m.group(1) if m.groups() else m.group(0)).strip()[:40]
                break
        for pat in self.ctx_patterns:
            m = pat.search(buffer)
            if m:
                self.ctx = (m.group(1) if m.groups() else m.group(0)).strip()[:16]
                break

    async def _send(self, ws, event_type: str, payload: dict, request_id: str = "") -> None:
        await self._send_raw(ws, self.make_event(event_type, payload, request_id))

    @staticmethod
    async def _send_raw(ws, data: dict) -> None:
        try:
            await ws.send(json.dumps(data))
        except Exception:
            pass

    # --- output scanning -------------------------------------------------------
    async def _feed(self, ws, clean: str) -> None:
        """Scan new (ANSI-free) agent output for prompts, completion and status."""
        self._scan = (self._scan + clean)[-4000:]
        self._scan_meta(self._scan)
        match = self._permission_match(self._scan) if self.detect_permissions else None
        if match is not None:
            prompt = self._scan.strip()[-200:]
            prompt_id = self._prompt_line(self._scan, match)
            # Whatever happens, this text has been seen: it must never match
            # again later (that re-reported answered prompts as phantom cards).
            self._scan = ""
            # Right after an answer the TUI may repaint the *same* prompt once
            # more; a different one is the agent's next question.
            echo = time.monotonic() < self._quiet_until and prompt_id == self._last_prompt_id
            if self._pending_request is None and not echo:
                self._cancel_flush()
                self._pending_request = uuid.uuid4().hex[:12]
                self._last_prompt_id = prompt_id
                await self._send(ws, "tool_request", {"tool": prompt, "args": ""},
                                 request_id=self._pending_request)
            return
        if self._looks_like_done(self._scan):
            self._scan = ""
            self._cancel_flush()  # a late status line must not undo "done"
            await self._send(ws, "completed", {"text": "done"})
            return
        if "\n" in clean:
            lines = [line for line in clean.splitlines() if line.strip()]
            if lines:
                await self._message(ws, lines[-1])

    async def _message(self, ws, line: str) -> None:
        line = line.strip()[:100]
        if not line or line == self._last_message:
            return
        self._last_message = line
        now = time.monotonic()
        if now - self._last_message_at >= MESSAGE_INTERVAL:
            self._last_message_at = now
            await self._send(ws, "message", {"text": line, **self._meta_payload()})
        elif self._flush_handle is None:
            self._flush_handle = asyncio.get_running_loop().call_later(
                MESSAGE_INTERVAL, lambda: asyncio.ensure_future(self._flush_message(ws))
            )

    def _cancel_flush(self) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None

    async def _flush_message(self, ws) -> None:
        self._flush_handle = None
        self._last_message_at = time.monotonic()
        await self._send(ws, "message", {"text": self._last_message, **self._meta_payload()})

    def _prompt_settled(self) -> None:
        """A prompt was answered (here or there): start clean."""
        self._pending_request = None
        self._scan = ""
        self._quiet_until = time.monotonic() + PROMPT_GRACE

    def _user_typed(self) -> None:
        """Keys typed in the terminal answer a pending prompt there: withdraw
        the island's card so it can't answer a second time."""
        request_id = self._pending_request
        if request_id is None:
            return
        self._prompt_settled()
        if self._ws is not None:
            asyncio.ensure_future(self._send_raw(self._ws, {"type": "cancel", "request_id": request_id}))

    async def _read_stdin(self, ws) -> None:
        """Read user input from terminal and forward to agent.

        readline() runs on a daemon thread: a worker-pool thread would keep
        the wrapper alive after the agent exits, until you pressed Enter.
        """
        loop = asyncio.get_running_loop()
        lines: "asyncio.Queue[str]" = asyncio.Queue()

        def pump() -> None:
            while True:
                try:
                    line = sys.stdin.readline()
                except Exception:
                    line = ""
                try:
                    loop.call_soon_threadsafe(lines.put_nowait, line)
                except RuntimeError:  # loop already closed
                    return
                if not line:
                    return

        threading.Thread(target=pump, daemon=True, name="dulus-bar-stdin").start()
        while True:
            line = await lines.get()
            if not line:
                break
            if self._agent_stdin is None:
                continue
            self._user_typed()
            self._agent_stdin.write(line.encode())
            await self._agent_stdin.drain()

    async def _read_stdout(self, process: asyncio.subprocess.Process, ws) -> None:
        if process.stdout is None:
            return
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            sys.stdout.write(text)
            sys.stdout.flush()
            if ws is not None:
                await self._feed(ws, text)

    async def _read_stderr(self, process: asyncio.subprocess.Process, ws) -> None:
        if process.stderr is None:
            return
        while True:
            chunk = await process.stderr.read(4096)
            if not chunk:
                break
            sys.stderr.write(chunk.decode("utf-8", errors="replace"))
            sys.stderr.flush()

    def _permission_match(self, buffer: str) -> Optional["re.Match[str]"]:
        for pattern in self.permission_patterns:
            match = pattern.search(buffer)
            if match:
                return match
        return None

    def _looks_like_permission(self, buffer: str) -> bool:
        return self._permission_match(buffer) is not None

    @staticmethod
    def _prompt_line(buffer: str, match: "re.Match[str]") -> str:
        """The whole line a prompt matched on: what tells two prompts apart."""
        start = buffer.rfind("\n", 0, match.start()) + 1
        end = buffer.find("\n", match.end())
        return " ".join(buffer[start:len(buffer) if end == -1 else end].split())

    def _looks_like_done(self, buffer: str) -> bool:
        return any(p.search(buffer) for p in self.done_patterns)

    def _answer(self, approved: bool) -> bytes:
        if self._pty_master is not None:
            return (self.answer_yes_pty if approved else self.answer_no_pty).encode()
        return (self.answer_yes_pipe if approved else self.answer_no_pipe).encode()

    async def _write_to_agent(self, data: bytes) -> None:
        """Send bytes to the agent's stdin, whichever transport is active."""
        if self._pty_master is not None:
            try:
                os.write(self._pty_master, data)
            except OSError:
                pass
            return
        if self._agent_stdin is not None:
            self._agent_stdin.write(data)
            try:
                await self._agent_stdin.drain()
            except Exception:
                pass

    async def _listen_decisions(self, ws) -> None:
        """Listen for decisions from the island and type them into the agent."""
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                kind = data.get("type")
                request_id = data.get("request_id")
                if kind == "request_resolved" and request_id and request_id == self._pending_request:
                    self._prompt_settled()  # e.g. withdrawn by the hub
                    continue
                if kind != "decision" or self._pending_request is None:
                    continue  # never type an answer when nothing is being asked
                if request_id:
                    if request_id != self._pending_request:
                        continue
                elif data.get("session_id") != self.session_id:
                    continue
                if self._agent_stdin is None and self._pty_master is None:
                    continue
                payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
                approved = bool(payload.get("approved"))
                self._prompt_settled()
                await self._write_to_agent(self._answer(approved))
                await self._send(ws, "tool_approved" if approved else "tool_denied", {"approved": approved})
        except websockets.exceptions.ConnectionClosed:
            pass

    async def run(self) -> int:
        if not self.command:
            print("[dulus-bar] no command provided", file=sys.stderr)
            return 1
        # Interactive CLIs (claude, grok, gemini, codex…) refuse to boot their
        # UI without a real TTY — under plain pipes they just sit there or exit.
        # On POSIX with a terminal attached, run the agent under a PTY so it
        # behaves exactly as if the user had launched it directly.
        if os.name == "posix" and sys.stdin.isatty():
            return await self._run_pty()
        return await self._run_pipes()

    async def _run_pty(self) -> int:
        """Spawn the agent under a pseudo-terminal (POSIX).

        The wrapper puts its own stdin in raw mode and bridges:
          user keys  -> pty master  (arrows, ctrl-c, tab completion all work)
          pty master -> user stdout + island feed (ANSI-stripped for matching)
        """
        import fcntl
        import pty
        import struct
        import termios
        import tty

        master, slave = pty.openpty()
        try:
            size = shutil.get_terminal_size((80, 24))
            fcntl.ioctl(
                slave, termios.TIOCSWINSZ,
                struct.pack("HHHH", size.lines, size.columns, 0, 0),
            )
        except Exception:
            pass

        env = os.environ.copy()
        env["DULUS_BAR_SESSION_ID"] = self.session_id
        env["DULUS_BAR_AGENT"] = self.agent_name  # agents using dulus_bar.client join this row
        env["VIBE_ISLAND_SESSION_ID"] = self.session_id  # back-compat
        env.setdefault("TERM", "xterm-256color")

        try:
            process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                preexec_fn=os.setsid,
                close_fds=True,
            )
        except Exception as exc:
            os.close(master)
            os.close(slave)
            print(f"[dulus-bar] failed to start {self.command}: {exc}", file=sys.stderr)
            return 1
        os.close(slave)
        self._pty_master = master

        ws = await self._connect_with_retry()
        if ws is None:
            print(
                f"[dulus-bar] couldn't reach Dulus Bar at {EVENT_SERVER}.\n"
                "  Start the island first:  dulusbar  (./dulusbar on macOS/Linux, connect.cmd on Windows)",
                file=sys.stderr,
            )
        self._ws = ws

        loop = asyncio.get_running_loop()
        out_q: "asyncio.Queue[bytes]" = asyncio.Queue()

        def _on_master_readable() -> None:
            try:
                data = os.read(master, 65536)
            except OSError:
                data = b""  # EIO = agent side closed
            out_q.put_nowait(data)

        try:
            os.set_blocking(master, False)
        except Exception:
            pass
        try:
            loop.add_reader(master, _on_master_readable)
        except Exception:
            os.close(master)
            self._pty_master = None
            print("[dulus-bar] PTY unsupported here; falling back to pipes", file=sys.stderr)
            return await self._run_pipes()

        # Raw-mode keyboard bridge so interactive UIs get arrows/ctrl keys.
        old_term = None
        stdin_fd = sys.stdin.fileno()
        try:
            old_term = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd)
        except Exception:
            old_term = None

        def _on_stdin_readable() -> None:
            try:
                data = os.read(stdin_fd, 4096)
            except OSError:
                data = b""
            if data:
                if is_user_input(data):
                    self._user_typed()
                try:
                    os.write(master, data)
                except OSError:
                    pass

        try:
            loop.add_reader(stdin_fd, _on_stdin_readable)
        except Exception:
            pass

        rc = 1
        try:
            if ws is not None:
                async with ws:
                    await self._send(ws, "session_started", {
                        "pid": process.pid,
                        "terminal_hint": self.agent_name,
                        "cwd": os.getcwd(),
                        **self._meta_payload(),
                    })
                    await asyncio.gather(
                        self._pump_pty(out_q, ws),
                        self._listen_decisions(ws),
                    )
            else:
                await self._pump_pty(out_q, None)
        except Exception as exc:
            print(f"[dulus-bar] websocket error: {exc}", file=sys.stderr)
        finally:
            self._ws = None
            for fd in (stdin_fd, master):
                try:
                    loop.remove_reader(fd)
                except Exception:
                    pass
            if old_term is not None:
                try:
                    termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_term)
                except Exception:
                    pass
            try:
                os.close(master)
            except OSError:
                pass
            self._pty_master = None
        try:
            rc = await process.wait()
        except Exception:
            pass
        return rc

    async def _pump_pty(self, out_q: "asyncio.Queue[bytes]", ws) -> None:
        """Forward PTY output to the user's terminal and the island."""
        stdout = sys.stdout.buffer
        while True:
            chunk = await out_q.get()
            if not chunk:
                if ws is not None:
                    await ws.close()  # agent exited: end _listen_decisions too
                return
            try:
                stdout.write(chunk)
                stdout.flush()
            except Exception:
                pass
            if ws is not None:
                await self._feed(ws, _strip_ansi(chunk.decode("utf-8", errors="replace")))

    async def _run_pipes(self) -> int:
        """Original pipe-based transport (Windows, or no TTY attached)."""

        # Resolve command on PATH if needed
        exe = self.command[0]
        if not os.path.isabs(exe) and not os.path.exists(exe):
            resolved = shutil.which(exe)
            if resolved:
                self.command[0] = resolved

        env = os.environ.copy()
        env["DULUS_BAR_SESSION_ID"] = self.session_id
        env["DULUS_BAR_AGENT"] = self.agent_name  # agents using dulus_bar.client join this row
        env["VIBE_ISLAND_SESSION_ID"] = self.session_id  # back-compat

        try:
            process = await asyncio.create_subprocess_exec(
                *self.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                env=env,
            )
        except Exception as exc:
            print(f"[dulus-bar] failed to start {self.command}: {exc}", file=sys.stderr)
            return 1

        self._agent_stdin = process.stdin

        ws = await self._connect_with_retry()
        if ws is None:
            print(
                f"[dulus-bar] couldn't reach Dulus Bar at {EVENT_SERVER}.\n"
                "  Start the island first:  dulusbar  (./dulusbar on macOS/Linux, connect.cmd on Windows)",
                file=sys.stderr,
            )
            # Still run the agent — just without the island feed
            stdin_task = asyncio.ensure_future(self._read_stdin(None))
            try:
                await asyncio.gather(self._read_stdout_no_ws(process), self._read_stderr(process, None))
            except Exception:
                pass
            finally:
                stdin_task.cancel()
            return await process.wait()

        self._ws = ws
        try:
            async with ws:
                await self._send(ws, "session_started", {
                    "pid": process.pid,
                    "terminal_hint": self.agent_name,
                    "cwd": os.getcwd(),
                    **self._meta_payload(),
                })
                # The agent's output decides when we're done; input and the
                # island listener stop with it instead of keeping us alive.
                background = [
                    asyncio.ensure_future(self._read_stdin(ws)),
                    asyncio.ensure_future(self._listen_decisions(ws)),
                ]
                try:
                    await asyncio.gather(self._read_stdout(process, ws), self._read_stderr(process, ws))
                finally:
                    for task in background:
                        task.cancel()
        except Exception as exc:
            print(f"[dulus-bar] websocket error: {exc}", file=sys.stderr)
        finally:
            self._ws = None

        return await process.wait()

    async def _connect_with_retry(self, attempts: int = 8, delay: float = 0.4):
        """Try a few times so connect.ps1 can race the island boot."""
        last_exc = None
        for i in range(attempts):
            try:
                return await websockets.connect(EVENT_SERVER, open_timeout=2)
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(delay)
        if last_exc:
            print(f"[dulus-bar] connect failed after {attempts} tries: {last_exc}", file=sys.stderr)
        return None

    async def _read_stdout_no_ws(self, process: asyncio.subprocess.Process) -> None:
        """Passthrough stdout when island is offline."""
        if process.stdout is None:
            return
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                break
            sys.stdout.write(chunk.decode("utf-8", errors="replace"))
            sys.stdout.flush()


async def main():
    if len(sys.argv) < 3:
        print("Usage: base_wrapper.py \"Agent Name\" /path/to/agent [args...]", file=sys.stderr)
        return 1
    agent_name = sys.argv[1]
    command = sys.argv[2:]
    wrapper = AgentWrapper(agent_name, command)
    return await wrapper.run()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
