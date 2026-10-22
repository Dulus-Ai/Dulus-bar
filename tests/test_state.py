"""The session reducer shared by the hub and the Qt island."""

import time

from dulus_bar.protocol import AgentEvent
from dulus_bar.state import Session, SessionStore, call_text, one_line


def ev(kind, session="s1", agent="Claude Code", request_id="", ts=0.0, **payload):
    return AgentEvent(agent=agent, event_type=kind, session_id=session,
                      payload=payload, request_id=request_id, ts=ts)


def test_session_lifecycle_and_metadata():
    store = SessionStore()
    store.apply(ev("session_started", cwd="/home/kev/dulus-bar", model="opus 4.5", pid=4242))
    s = store.get("Claude Code", "s1")
    assert (s.status, s.title, s.model, s.pid) == ("running", "dulus-bar", "opus 4.5", 4242)
    store.apply(ev("prompt", text="fix   the\nlogin bug"))
    assert s.message == "fix the login bug"
    store.apply(ev("tool_started", tool="Bash", args="npm test"))
    assert s.message == "Bash · npm test"
    store.apply(ev("completed", text="All green", ctx="82k"))
    assert (s.status, s.message, s.ctx) == ("done", "All green", "82k")
    store.apply(ev("session_ended"))
    assert store.sessions == {}


def test_waiting_is_exactly_having_a_pending_request():
    store = SessionStore()
    store.apply(ev("tool_request", request_id="r1", tool="Bash", args="rm -rf build/"))
    s = store.get("Claude Code", "s1")
    assert s.status == "waiting" and s.message == "Bash  rm -rf build/"
    # Chatter while the prompt is up doesn't hide it.
    store.apply(ev("message", text="thinking…"))
    assert s.status == "waiting"
    assert store.resolve("r1", "approved").request_id == "r1"
    assert (s.status, s.message) == ("running", "Approved")


def test_requests_queue_fifo_and_dedupe_by_id():
    store = SessionStore()
    store.apply(ev("tool_request", session="a", request_id="r1", tool="Bash"))
    store.apply(ev("tool_request", session="b", agent="Codex", request_id="r2", tool="Edit"))
    store.apply(ev("tool_request", session="a", request_id="r1", tool="Bash"))
    assert [p.request_id for p in store.permissions] == ["r1", "r2"]
    assert store.foremost().session_id == "a"  # the oldest prompt leads
    store.resolve("r1")
    assert store.current_permission().request_id == "r2"
    assert store.foremost().agent == "Codex"


def test_moving_on_answers_the_prompt_elsewhere():
    store = SessionStore()
    store.apply(ev("tool_request", request_id="r1", tool="Bash", args="npm test"))
    store.apply(ev("tool_request", request_id="r2", tool="Edit", args="app.py"))
    # Running the tool means it was approved in the terminal.
    answered = store.apply(ev("tool_finished", tool="Bash", args="npm test"))
    assert [p.request_id for p in answered] == ["r1"]
    # A new prompt from the user means the turn ended: nothing is pending.
    answered = store.apply(ev("prompt", text="no, do it differently"))
    assert [p.request_id for p in answered] == ["r2"]
    assert store.permissions == []


def test_a_late_hook_event_never_steps_back():
    store = SessionStore()
    now = time.time()
    store.apply(ev("completed", ts=now, text="Done"))
    store.apply(ev("tool_started", ts=now - 1, tool="Bash", args="ls"))  # lost a race
    assert store.get("Claude Code", "s1").status == "done"
    # …but a permission request is never dropped.
    store.apply(ev("tool_request", ts=now - 1, request_id="r1", tool="Bash"))
    assert len(store.permissions) == 1


def test_removal_events_drop_the_session_and_its_requests():
    for kind in ("disconnected", "session_ended", "session_removed"):
        store = SessionStore()
        store.apply(ev("tool_request", request_id="r1", tool="Bash"))
        dropped = store.apply(ev(kind))
        assert [p.request_id for p in dropped] == ["r1"]
        assert store.sessions == {} and store.permissions == []


def test_request_resolved_event_and_health_noise():
    store = SessionStore()
    store.apply(ev("tool_request", request_id="r1", tool="Bash"))
    store.apply(ev("request_resolved", request_id="r1", outcome="denied"))
    assert store.permissions == [] and store.get("Claude Code", "s1").message == "Denied"
    store.apply(ev("ping", agent="_health", session="_health"))
    store.apply(ev("message", agent="DulusHealth", text="ok"))
    assert list(store.sessions) == [("Claude Code", "s1")]


def test_prune_respects_liveness():
    store = SessionStore()
    store.apply(ev("session_started", session="alive"))
    store.apply(ev("session_started", session="dead", pid=99999))
    store.apply(ev("session_started", session="quiet"))
    store.apply(ev("tool_request", session="asking", request_id="r1", tool="Bash"))
    for s in store.sessions.values():
        s.updated -= 3600
    verdicts = {"alive": True, "dead": False}
    removed = store.prune(time.time(), 1800, lambda s: verdicts.get(s.session_id))
    assert sorted(sid for _, sid in removed) == ["dead", "quiet"]
    assert sorted(sid for _, sid in store.sessions) == ["alive", "asking"]


def test_ordering_and_snapshot_round_trip():
    store = SessionStore()
    store.apply(ev("completed", session="done"))
    store.apply(ev("session_started", session="busy"))
    store.apply(ev("error", session="broken", text="boom"))
    store.apply(ev("tool_request", session="asking", request_id="r1", tool="Bash"))
    assert [s.session_id for s in store.ordered()] == ["asking", "broken", "busy", "done"]
    assert sorted(sid for _, sid in store.finished()) == ["broken", "done"]
    copy = SessionStore()
    copy.load(store.snapshot())
    assert copy.snapshot() == store.snapshot()
    assert isinstance(copy.get("Claude Code", "busy"), Session)


def test_call_text_never_repeats_itself():
    assert call_text("Bash", "rm -rf build/") == "Bash  rm -rf build/"
    assert call_text("Run rm -rf build?", "rm -rf build") == "Run rm -rf build?"
    assert call_text("", "ls") == "ls" and call_text("Bash", "") == "Bash"
    assert one_line("a\n\n b  c", 4) == "a b…"


def test_first_line_drops_markdown():
    from dulus_bar.text import first_line

    assert first_line("\n## **Fixed** the `parser`\nmore") == "Fixed the parser"
    assert first_line("- snake_case stays") == "snake_case stays"
    assert first_line("") == ""
