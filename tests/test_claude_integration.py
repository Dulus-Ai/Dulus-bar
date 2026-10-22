"""Claude Code hooks: event mapping, Allow / Deny output, and the installer."""

import io
import json
import os
import stat
import sys
import threading
import time

import pytest

from conftest import free_port, wait_for
from dulus_bar.integrations import ConfigError, shell_command
from dulus_bar.integrations import claude


@pytest.fixture
def hub_url(hub, monkeypatch):
    server, url = hub
    monkeypatch.setenv("DULUS_BAR_SERVER", url)
    return server


def hook(name, **extra):
    data = {"session_id": "sess-1", "transcript_path": "", "cwd": "/home/kev/dulus-bar",
            "hook_event_name": name}
    data.update(extra)
    return data


def test_status_hooks_drive_the_session(hub_url, tmp_path):
    server = hub_url
    store = server.store
    key = ("Claude Code", "sess-1")

    claude.handle(hook("SessionStart", source="startup", model="claude-opus-4-5-20251101"))
    assert wait_for(lambda: key in store.sessions)
    s = store.sessions[key]
    assert (s.status, s.title, s.model) == ("running", "dulus-bar", "opus 4.5")
    assert s.pid and s.pid_host

    claude.handle(hook("UserPromptSubmit", prompt="fix the login bug"))
    assert wait_for(lambda: s.message == "fix the login bug")

    claude.handle(hook("PreToolUse", tool_name="Bash", tool_input={"command": "npm test"}))
    assert wait_for(lambda: s.message == "Bash · npm test")

    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in (
        {"type": "assistant", "message": {"model": "claude-sonnet-4-20250514",
                                          "usage": {"input_tokens": 10}}},
        {"type": "assistant", "isSidechain": True, "message": {"model": "x", "usage": {"input_tokens": 1}}},
        {"type": "assistant", "message": {"model": "claude-opus-4-5-20251101", "usage": {
            "input_tokens": 3, "cache_read_input_tokens": 80000, "cache_creation_input_tokens": 1997}}},
        {"type": "assistant", "message": {"model": "<synthetic>", "usage": {"input_tokens": 0}}},
    )) + "\n")
    claude.handle(hook("PostToolUse", tool_name="Bash", tool_input={"command": "npm test"},
                       tool_response={}, transcript_path=str(transcript)))
    assert wait_for(lambda: s.ctx == "82k")

    claude.handle(hook("Stop", last_assistant_message="## All tests pass\nDetails…",
                       transcript_path=str(transcript)))
    assert wait_for(lambda: s.status == "done")
    assert (s.message, s.model) == ("All tests pass", "opus 4.5")

    claude.handle(hook("Notification", notification_type="idle_prompt",
                       message="Claude is waiting for your input"))
    assert wait_for(lambda: s.message == "Claude is waiting for your input")

    claude.handle(hook("SessionEnd", reason="prompt_input_exit"))
    assert wait_for(lambda: key not in store.sessions)


def _decide_when_asked(server, **decision):
    def run():
        assert wait_for(lambda: server.store.permissions, timeout=3)
        perm = server.store.permissions[0]
        server.decide(perm.request_id, **decision)

    thread = threading.Thread(target=run)
    thread.start()
    return thread


SUGGESTIONS = [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "npm test:*"}],
                "behavior": "allow", "destination": "localSettings"}]


@pytest.mark.parametrize("decision,expected", [
    ({"approved": True}, {"behavior": "allow"}),
    ({"approved": True, "always": True}, {"behavior": "allow", "updatedPermissions": SUGGESTIONS}),
    ({"approved": False}, {"behavior": "deny", "message": claude.DENY_MESSAGE}),
])
def test_permission_request_answers_claude(hub_url, decision, expected):
    server = hub_url
    out = io.StringIO()
    thread = _decide_when_asked(server, **decision)
    code = claude.handle(hook("PermissionRequest", tool_name="Bash",
                              tool_input={"command": "npm test"}, permission_suggestions=SUGGESTIONS), out)
    thread.join(3)
    assert code == 0
    assert json.loads(out.getvalue()) == {
        "hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": expected}
    }


def test_permission_card_carries_the_details(hub_url):
    server = hub_url
    thread = threading.Thread(target=claude.handle, args=(hook(
        "PermissionRequest", tool_name="Bash", permission_suggestions=SUGGESTIONS,
        tool_input={"command": "rm -rf build/", "description": "Clean"}), io.StringIO()))
    thread.start()
    assert wait_for(lambda: server.store.permissions)
    perm = server.store.permissions[0]
    assert (perm.tool, perm.args, perm.detail) == ("Bash", "rm -rf build/", "rm -rf build/\n# Clean")
    assert perm.can_always and perm.always_label == "Bash(npm test:*)"
    assert server.store.sessions[("Claude Code", "sess-1")].title == "dulus-bar"
    server.decide(perm.request_id, False)
    thread.join(3)


def test_no_island_means_no_output_and_no_delay(monkeypatch):
    monkeypatch.setenv("DULUS_BAR_SERVER", f"ws://127.0.0.1:{free_port()}")
    out = io.StringIO()
    started = time.monotonic()
    assert claude.handle(hook("PermissionRequest", tool_name="Bash", tool_input={}), out) == 0
    assert claude.handle(hook("PreToolUse", tool_name="Bash", tool_input={}), out) == 0
    assert out.getvalue() == "" and time.monotonic() - started < 1


def test_answered_in_the_terminal_means_no_output(hub_url):
    server = hub_url
    out = io.StringIO()
    thread = threading.Thread(target=claude.handle, args=(hook(
        "PermissionRequest", tool_name="Bash", tool_input={"command": "ls"}), out))
    thread.start()
    assert wait_for(lambda: server.store.permissions)
    claude.handle(hook("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_response={}))
    thread.join(3)
    assert not thread.is_alive() and out.getvalue() == ""


def test_hook_main_never_breaks_claude():
    for raw in (b"", b"not json", b"[1, 2]", b'{"hook_event_name": "Stop"}'):
        assert claude.hook_main(io.BytesIO(raw), io.StringIO()) == 0


def test_helpers():
    assert claude.pretty_model("claude-opus-4-5-20251101") == "opus 4.5"
    assert claude.pretty_model("claude-sonnet-4-20250514") == "sonnet 4"
    assert claude.pretty_model("claude-fable-5-1[1m]") == "fable 5.1 1M"
    assert claude.pretty_model("gpt-5") == "gpt-5"
    assert [claude.format_tokens(n) for n in (950, 8200, 81_997, 1_250_000)] == ["950", "8.2k", "82k", "1.2M"]


def test_agent_pid_skips_the_shell_in_between(monkeypatch):
    chain = {7: ("python3", 100), 100: ("bash", 50), 50: ("claude", 1)}
    monkeypatch.setattr(claude.os, "getpid", lambda: 7)
    monkeypatch.setattr(claude.os, "getppid", lambda: 100)
    monkeypatch.setattr(claude, "_proc", lambda pid: chain.get(pid, ("", 0)))
    assert claude.agent_pid() == 50


def test_agent_pid_skips_our_own_launcher(monkeypatch):
    # A onefile bootloader (or the Windows venv redirector) runs us as its child.
    chain = {7: ("DulusBar", 8), 8: ("DulusBar", 9), 9: ("bash", 50), 50: ("claude", 1)}
    monkeypatch.setattr(claude.os, "getpid", lambda: 7)
    monkeypatch.setattr(claude.os, "getppid", lambda: 8)
    monkeypatch.setattr(claude, "_proc", lambda pid: chain.get(pid, ("", 0)))
    assert claude.agent_pid() == 50


# --- installer ----------------------------------------------------------------


def _ours(settings):
    return {
        event: [h for g in groups for h in g["hooks"] if claude.is_ours(h)]
        for event, groups in settings["hooks"].items()
    }


def test_install_merges_without_touching_anything_else(isolated_home):
    path = claude.settings_path()
    path.parent.mkdir(parents=True)
    original = {
        "model": "opus",
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
        },
    }
    path.write_text(json.dumps(original))

    result = claude.install()
    settings = json.loads(path.read_text())
    assert settings["model"] == "opus"
    assert {"type": "command", "command": "my-linter"} in settings["hooks"]["PreToolUse"][0]["hooks"]
    assert {"type": "command", "command": "say done"} in settings["hooks"]["Stop"][0]["hooks"]
    ours = _ours(settings)
    assert set(claude.HOOK_EVENTS) <= {e for e, hooks in ours.items() if hooks}
    permission = ours["PermissionRequest"][0]
    assert permission["timeout"] == 86400 and "async" not in permission
    assert ours["PreToolUse"][0]["async"] is True
    group = next(g for g in settings["hooks"]["PreToolUse"] if claude.is_ours(g["hooks"][0]))
    assert group["matcher"] == "*"
    assert result["command"].endswith("main.py hook claude")
    assert path.with_name("settings.json.dulus-bar.bak").exists()

    claude.install()  # idempotent
    assert all(len(hooks) <= 1 for hooks in _ours(json.loads(path.read_text())).values())

    st = claude.status()
    assert st["installed"] and st["current"] and not st["missing"]

    assert claude.uninstall()["removed"] == len(claude.HOOK_EVENTS)
    assert json.loads(path.read_text()) == original


def test_install_refuses_to_clobber_a_broken_file(isolated_home):
    path = claude.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")
    with pytest.raises(ConfigError):
        claude.install()
    assert path.read_text() == "{ not json"
    assert "error" in claude.status()


def test_status_spots_partial_installs(isolated_home):
    claude.install()
    path = claude.settings_path()
    settings = json.loads(path.read_text())
    del settings["hooks"]["Stop"]
    path.write_text(json.dumps(settings))
    st = claude.status()
    assert st["partial"] and st["missing"] == ["Stop"] and not st["installed"]


def test_shell_quoting():
    assert shell_command(["/opt/my python/bin/python3", "/x/dulus_bar/main.py", "hook", "claude"]) == \
        "'/opt/my python/bin/python3' /x/dulus_bar/main.py hook claude"
    assert shell_command([r"C:\Py 3\python.exe", "it's.py"], "powershell") == \
        r"& 'C:\Py 3\python.exe' 'it''s.py'"
    assert claude.is_ours({"command": r"& 'C:\Py\python.exe' 'C:\dulus_bar\main.py' 'hook' 'claude'"})
    assert not claude.is_ours({"command": "my-linter"})


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks and modes")
def test_install_keeps_symlinks_and_permissions(isolated_home):
    real = isolated_home / "dotfiles" / "claude-settings.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-secret"}}))
    os.chmod(real, 0o600)
    link = claude.settings_path()
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    claude.install()
    assert link.is_symlink()  # still pointing into the dotfiles repo
    assert stat.S_IMODE(real.stat().st_mode) == 0o600
    assert json.loads(real.read_text())["env"]["ANTHROPIC_API_KEY"] == "sk-secret"
    assert claude.status()["installed"]
