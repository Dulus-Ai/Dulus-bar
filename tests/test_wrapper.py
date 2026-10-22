"""The PTY / pipe wrapper: one prompt, one card — and no stray keystrokes."""

import asyncio
import json

from dulus_bar.wrappers import base_wrapper as bw
from dulus_bar.wrappers.base_wrapper import AgentWrapper


class FakeWS:
    def __init__(self, incoming=()):
        self.sent = []
        self._incoming = list(incoming)

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise StopAsyncIteration
        return json.dumps(self._incoming.pop(0))

    def kinds(self):
        return [m.get("type") for m in self.sent]


def test_a_redrawn_prompt_is_reported_once():
    async def scenario():
        w = AgentWrapper("Dulus", ["dulus"])
        ws = FakeWS()
        for _ in range(5):  # the TUI keeps repainting the same prompt
            await w._feed(ws, "Run this tool? Allow this action? (Y/n)\n")
        return w, ws

    w, ws = asyncio.run(scenario())
    assert ws.kinds().count("tool_request") == 1
    assert ws.sent[0]["request_id"] == w._pending_request


def test_decisions_only_answer_a_pending_prompt(monkeypatch):
    async def scenario():
        w = AgentWrapper("Dulus", ["dulus"])
        typed = []

        async def capture(data):
            typed.append(data)

        w._write_to_agent = capture
        w._agent_stdin = object()  # pretend pipes are wired
        # Nothing pending: a stray decision must not type anything.
        await w._listen_decisions(FakeWS([{"type": "decision", "session_id": w.session_id,
                                           "payload": {"approved": True}}]))
        assert typed == []
        await w._feed(FakeWS(), "Allow this action? (Y/n)\n")
        rid = w._pending_request
        ws = FakeWS([
            {"type": "decision", "request_id": "someone-else", "payload": {"approved": True}},
            {"type": "decision", "request_id": rid, "payload": {"approved": False}},
            {"type": "decision", "request_id": rid, "payload": {"approved": True}},  # duplicate
        ])
        await w._listen_decisions(ws)
        return w, typed, ws

    w, typed, ws = asyncio.run(scenario())
    assert typed == [b"n\n"]  # exactly one answer, the right one
    assert ws.kinds() == ["tool_denied"]
    assert w._pending_request is None


def test_after_an_answer_the_echoed_prompt_is_not_a_new_one():
    async def scenario():
        w = AgentWrapper("Dulus", ["dulus"])
        w._agent_stdin = object()

        async def ignore(data):
            pass

        w._write_to_agent = ignore
        ws = FakeWS()
        await w._feed(ws, "Allow this action? (Y/n)\n")
        await w._listen_decisions(FakeWS([{"type": "decision", "request_id": w._pending_request,
                                           "payload": {"approved": True}}]))
        await w._feed(ws, "Allow this action? (Y/n)\n")  # the TUI's echo
        return ws

    assert asyncio.run(scenario()).kinds().count("tool_request") == 1


def test_typing_in_the_terminal_withdraws_the_card():
    async def scenario():
        w = AgentWrapper("Dulus", ["dulus"])
        ws = FakeWS()
        w._ws = ws
        await w._feed(ws, "Allow this action? (Y/n)\n")
        rid = w._pending_request
        w._user_typed()
        await asyncio.sleep(0)
        return w, ws, rid

    w, ws, rid = asyncio.run(scenario())
    assert w._pending_request is None
    assert ws.sent[-1] == {"type": "cancel", "request_id": rid}


def test_status_lines_are_coalesced(monkeypatch):
    monkeypatch.setattr(bw, "MESSAGE_INTERVAL", 0.05)

    async def scenario():
        w = AgentWrapper("Dulus", ["dulus"])
        ws = FakeWS()
        for n in range(20):
            await w._feed(ws, f"step {n}\n")
        await asyncio.sleep(0.12)
        return ws

    ws = asyncio.run(scenario())
    texts = [m["payload"]["text"] for m in ws.sent if m["type"] == "message"]
    assert texts[0] == "step 0" and texts[-1] == "step 19"  # first and latest, not all 20
    assert len(texts) <= 3


def test_an_answered_prompt_never_comes_back_as_a_phantom(monkeypatch):
    monkeypatch.setattr(bw, "PROMPT_GRACE", 0.05)

    async def scenario():
        w = AgentWrapper("Dulus", ["dulus"])
        w._agent_stdin = object()

        async def ignore(data):
            pass

        w._write_to_agent = ignore
        ws = FakeWS()
        await w._feed(ws, "Allow this action? (Y/n)\n")
        # The prompt is repainted while pending, and again during the grace.
        await w._feed(ws, "Allow this action? (Y/n)\n")
        await w._listen_decisions(FakeWS([{"type": "decision", "request_id": w._pending_request,
                                           "payload": {"approved": True}}]))
        await w._feed(ws, "Allow this action? (Y/n)\n")
        await asyncio.sleep(0.1)  # grace over: ordinary output must not re-match old text
        await w._feed(ws, "compiling…\n")
        return ws

    assert asyncio.run(scenario()).kinds().count("tool_request") == 1


def test_a_late_status_line_cannot_undo_done(monkeypatch):
    monkeypatch.setattr(bw, "MESSAGE_INTERVAL", 0.05)

    async def scenario():
        w = AgentWrapper("Dulus", ["dulus"])
        ws = FakeWS()
        await w._feed(ws, "step 1\n")
        await w._feed(ws, "step 2\n")  # throttled: a flush is scheduled
        await w._feed(ws, "✓ Done\n")
        await asyncio.sleep(0.12)
        return ws

    assert asyncio.run(scenario()).kinds()[-1] == "completed"


def test_answers_never_press_enter_to_say_no():
    w = AgentWrapper("Dulus", ["dulus"])
    w._pty_master = 99
    assert w._answer(True) == b"Y\r" and w._answer(False) == b"n"
    w._pty_master = None
    assert (w._answer(True), w._answer(False)) == (b"Y\n", b"n\n")
    for name in ("Claude Code", "Codex", "Gemini", "Qwen Code"):
        menu = AgentWrapper(name, ["x"])
        menu._pty_master = 99
        assert (menu._answer(True), menu._answer(False)) == (b"\r", b"\x1b")


def test_codex_prompts_are_detected():
    w = AgentWrapper("Codex", ["codex"])
    assert w._looks_like_permission("Would you like to run the following command?\n  npm test")


def test_terminal_generated_input_is_not_an_answer():
    assert not bw.is_user_input(b"\x1b[I") and not bw.is_user_input(b"\x1b[<0;3;4M")
    assert not bw.is_user_input(b"\x1b[12;40R") and not bw.is_user_input(b"\x1b]11;rgb:0/0/0\x07")
    assert bw.is_user_input(b"y") and bw.is_user_input(b"\r") and bw.is_user_input(b"\x1b[A")


def test_the_next_question_right_after_an_answer_is_not_mistaken_for_an_echo():
    async def scenario():
        w = AgentWrapper("Dulus", ["dulus"])
        w._agent_stdin = object()

        async def ignore(data):
            pass

        w._write_to_agent = ignore
        ws = FakeWS()
        await w._feed(ws, "Delete build/? (y/N)\n")
        await w._listen_decisions(FakeWS([{"type": "decision", "request_id": w._pending_request,
                                           "payload": {"approved": True}}]))
        await w._feed(ws, "Delete build/? (y/N)\n")  # echo of the answered prompt
        await w._feed(ws, "Push to origin? (y/N)\n")  # a new question, within the grace
        return ws

    sent = asyncio.run(scenario()).sent
    prompts = [m["payload"]["tool"] for m in sent if m["type"] == "tool_request"]
    assert len(prompts) == 2 and "Push to origin?" in prompts[1]
