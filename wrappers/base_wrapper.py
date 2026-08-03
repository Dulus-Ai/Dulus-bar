"""Base wrapper for any CLI agent. Launch the agent through this to pipe events to Dulus Bar.

Usage:
    python base_wrapper.py "Agent Name" /path/to/agent-bin [agent args...]

The wrapper:
  - Opens websocket to ws://127.0.0.1:17372.
  - Spawns the real agent with piped stdio.
  - Forwards your terminal input to the agent.
  - Forwards agent output to your terminal.
  - Sends status/permission events to the island.
  - Listens for Allow/Deny decisions from the island and injects them into the agent stdin.

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
import uuid
from typing import List, Optional

import websockets


# New name first, old name kept as a fallback for existing shells.
EVENT_SERVER = (
    os.getenv("DULUS_BAR_SERVER")
    or os.getenv("VIBE_ISLAND_SERVER")
    or "ws://127.0.0.1:17372"
)


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
        ]
        self.done_patterns = [
            re.compile(r"✓\s+Done"),
        ]
        self._agent_stdin: Optional[asyncio.StreamWriter] = None
        self._pending_decision: Optional[bool] = None

    def make_event(self, event_type: str, payload: dict) -> dict:
        return {
            "agent": self.agent_name,
            "type": event_type,
            "session_id": self.session_id,
            "payload": payload,
        }

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

    async def _send(self, ws, event_type: str, payload: dict) -> None:
        try:
            await ws.send(json.dumps(self.make_event(event_type, payload)))
        except Exception:
            pass

    async def _read_stdin(self, ws) -> None:
        """Read user input from terminal and forward to agent."""
        loop = asyncio.get_event_loop()
        while True:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except EOFError:
                break
            if not line:
                break
            if self._agent_stdin is None:
                continue
            self._agent_stdin.write(line.encode())
            await self._agent_stdin.drain()

    async def _read_stdout(self, process: asyncio.subprocess.Process, ws) -> None:
        if process.stdout is None:
            return
        buffer = ""
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            sys.stdout.write(text)
            sys.stdout.flush()

            if ws is None:
                continue

            buffer += text
            if self._looks_like_permission(buffer):
                await self._send(ws, "tool_request", {"tool": buffer.strip()[-200:], "args": ""})
                buffer = buffer[-500:]
            elif self._looks_like_done(buffer):
                await self._send(ws, "completed", {"text": "done"})
                buffer = buffer[-500:]
            elif "\n" in text:
                self._scan_meta(buffer)
                line = text.rsplit("\n", 2)[-2]
                if line.strip():
                    await self._send(ws, "message", {"text": line.strip()[:100], **self._meta_payload()})

    async def _read_stderr(self, process: asyncio.subprocess.Process, ws) -> None:
        if process.stderr is None:
            return
        while True:
            chunk = await process.stderr.read(4096)
            if not chunk:
                break
            sys.stderr.write(chunk.decode("utf-8", errors="replace"))
            sys.stderr.flush()

    def _looks_like_permission(self, buffer: str) -> bool:
        return any(p.search(buffer) for p in self.permission_patterns)

    def _looks_like_done(self, buffer: str) -> bool:
        return any(p.search(buffer) for p in self.done_patterns)

    async def _listen_decisions(self, ws) -> None:
        """Listen for decisions from the island and inject them into the agent stdin."""
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data.get("type") != "decision":
                    continue
                if data.get("session_id") != self.session_id:
                    continue
                approved = data.get("payload", {}).get("approved", False)
                if self._agent_stdin is None:
                    continue
                answer = "Y\n" if approved else "n\n"
                self._agent_stdin.write(answer.encode())
                await self._agent_stdin.drain()
                await self._send(ws, "tool_approved" if approved else "tool_denied", {"approved": approved})
        except websockets.exceptions.ConnectionClosed:
            pass

    async def run(self) -> int:
        if not self.command:
            print("[dulus-bar] no command provided", file=sys.stderr)
            return 1

        # Resolve command on PATH if needed
        exe = self.command[0]
        if not os.path.isabs(exe) and not os.path.exists(exe):
            resolved = shutil.which(exe)
            if resolved:
                self.command[0] = resolved

        env = os.environ.copy()
        env["DULUS_BAR_SESSION_ID"] = self.session_id
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
                f"[dulus-bar] no pude conectar a {EVENT_SERVER}.\n"
                "  Abre la barra primero:  dulusbar  (o ./dulusbar en mac/Linux, connect.cmd en Windows)",
                file=sys.stderr,
            )
            # Still run the agent — just without the island feed
            try:
                await asyncio.gather(
                    self._read_stdout_no_ws(process),
                    self._read_stderr(process, None),
                    self._read_stdin(None),
                )
            except Exception:
                pass
            return await process.wait()

        try:
            async with ws:
                await self._send(ws, "session_started", {
                    "pid": process.pid,
                    "terminal_hint": self.agent_name,
                    **self._meta_payload(),
                })
                await asyncio.gather(
                    self._read_stdout(process, ws),
                    self._read_stderr(process, ws),
                    self._read_stdin(ws),
                    self._listen_decisions(ws),
                )
        except Exception as exc:
            print(f"[dulus-bar] websocket error: {exc}", file=sys.stderr)

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
