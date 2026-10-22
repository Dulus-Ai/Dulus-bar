"""`dulusbar` subcommands, end to end against a real hub."""

import io
import json
import os
import subprocess
import sys
import threading

import pytest

from conftest import ROOT, free_port, wait_for
from dulus_bar import __version__, cli
from dulus_bar.server import AgentEventServer


@pytest.fixture
def published_hub(monkeypatch):
    """A hub that advertises itself in hub.json, like the real app."""
    server = AgentEventServer(port=free_port(), publish=True)
    assert server.start()
    yield server
    server.stop()


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_notify_posts_a_status(published_hub):
    assert cli.main(["notify", "--agent", "CI", "--title", "api", "Deploy", "finished"]) == 0
    assert wait_for(lambda: ("CI", "notify-CI") in published_hub.store.sessions)
    s = published_hub.store.sessions[("CI", "notify-CI")]
    assert (s.status, s.message, s.title) == ("done", "Deploy finished", "api")


def test_notify_without_an_island_never_fails_a_script(monkeypatch, capsys):
    monkeypatch.setenv("DULUS_BAR_PORT", str(free_port()))
    assert cli.main(["notify", "hello"]) == 0
    assert "isn't running" in capsys.readouterr().err
    assert cli.main(["notify", "-q", "hello"]) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("approved,code", [(True, 0), (False, 1)])
def test_ask_exit_code_is_the_answer(published_hub, approved, code):
    def answer():
        assert wait_for(lambda: published_hub.store.permissions, timeout=3)
        perm = published_hub.store.permissions[0]
        assert perm.tool == "Run migrations on prod?" and perm.detail == "alembic upgrade head"
        published_hub.decide(perm.request_id, approved)

    thread = threading.Thread(target=answer)
    thread.start()
    assert cli.main(["ask", "--detail", "alembic upgrade head", "Run", "migrations", "on", "prod?"]) == code
    thread.join(3)
    # The question is gone from the island once answered.
    assert wait_for(lambda: not published_hub.store.sessions)


def test_ask_without_an_island(monkeypatch):
    monkeypatch.setenv("DULUS_BAR_PORT", str(free_port()))
    assert cli.main(["ask", "--no-tty", "Deploy?"]) == 2
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    assert cli.main(["ask", "Deploy?"]) == 0  # falls back to asking in the terminal


def test_status_lists_sessions(published_hub, capsys):
    cli.main(["notify", "--status", "running", "--agent", "Dulus", "Thinking"])
    assert wait_for(lambda: published_hub.store.sessions)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Dulus" in out and "Thinking" in out
    assert cli.main(["status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["sessions"][0]["agent"] == "Dulus"


def test_status_and_quit_without_an_island(monkeypatch):
    monkeypatch.setenv("DULUS_BAR_PORT", str(free_port()))
    assert cli.main(["status"]) == 2
    assert cli.main(["quit"]) == 0


def test_quit_stops_the_island(published_hub):
    stopped = threading.Event()
    published_hub.on_shutdown(stopped.set)
    assert cli.main(["quit"]) == 0
    assert stopped.wait(2)


def test_hooks_install_status_uninstall(isolated_home, capsys):
    assert cli.main(["hooks", "install", "claude"]) == 0
    assert "Claude Code: connected" in capsys.readouterr().out
    assert cli.main(["hooks", "status", "claude"]) == 0
    assert "✓ Claude Code: connected" in capsys.readouterr().out
    assert cli.main(["hooks", "uninstall", "claude"]) == 0
    assert "disconnected" in capsys.readouterr().out
    assert cli.main(["hooks", "install", "codex"]) == 0
    assert cli.main(["hooks", "status", "all"]) == 0
    out = capsys.readouterr().out
    assert "✓ Codex: connected" in out and "· Claude Code: not connected" in out


def test_hook_subcommand_is_the_hook(monkeypatch, published_hub):
    data = {"session_id": "s9", "hook_event_name": "UserPromptSubmit", "prompt": "hi", "cwd": "/tmp/x"}
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps(data).encode())))
    assert cli.main(["hook", "claude"]) == 0
    assert wait_for(lambda: ("Claude Code", "s9") in published_hub.store.sessions)


def test_wrap_needs_a_command(capsys):
    assert cli.main(["wrap", "Codex"]) == 2
    assert "usage" in capsys.readouterr().err


def test_doctor_runs(published_hub, capsys):
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "Dulus Bar" in out and "island" in out and "Claude Code" in out


def test_doctor_survives_a_redirected_stdout():
    """`dulusbar doctor > log.txt` used to die with UnicodeEncodeError: on
    Windows a redirected stdout is cp1252, where '✓' cannot be encoded."""
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env["DULUS_BAR_PORT"] = str(free_port())  # no island to probe
    proc = subprocess.run(
        [sys.executable, "-m", "dulus_bar", "doctor"],
        cwd=str(ROOT), capture_output=True, timeout=180, env=env,
    )
    err = proc.stderr.decode("utf-8", "replace")
    assert "UnicodeEncodeError" not in err, err
    assert "Traceback" not in err, err
    assert proc.returncode in (0, 1), err
    out = proc.stdout.decode("utf-8")  # strict: the output really is UTF-8 now
    assert "Dulus Bar" in out
    assert "✓" in out or "✗" in out
