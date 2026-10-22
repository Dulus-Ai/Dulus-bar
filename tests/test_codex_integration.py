"""Codex notify: the payload handler and the config.toml installer."""

import json
import sys

import pytest

from conftest import wait_for
from dulus_bar.integrations import ConfigError
from dulus_bar.integrations import codex


def test_turn_complete_lights_up_the_island(hub, monkeypatch):
    server, url = hub
    monkeypatch.setenv("DULUS_BAR_SERVER", url)
    payload = {"type": "agent-turn-complete", "thread-id": "t-1", "turn-id": "u-1",
               "cwd": "/home/kev/api", "input-messages": ["fix it"],
               "last-assistant-message": "\n**Fixed the parser.**\nAlso added tests."}
    assert codex.handle_notify(json.dumps(payload)) == 0
    assert wait_for(lambda: ("Codex", "t-1") in server.store.sessions)
    s = server.store.sessions[("Codex", "t-1")]
    assert (s.status, s.message, s.title) == ("done", "Fixed the parser.", "api")


def test_other_payloads_are_ignored(hub, monkeypatch):
    server, url = hub
    monkeypatch.setenv("DULUS_BAR_SERVER", url)
    for raw in ("", "nope", json.dumps({"type": "something-else"}), "[1]"):
        assert codex.handle_notify(raw) == 0
    assert server.store.sessions == {}


def _toml(path):
    if sys.version_info < (3, 11):
        pytest.skip("tomllib needs Python 3.11+")
    import tomllib

    return tomllib.loads(path.read_text())


def test_install_writes_a_top_level_notify(isolated_home):
    path = codex.config_path()
    path.parent.mkdir(parents=True)
    path.write_text('model = "gpt-5"\n\n[profiles.fast]\nmodel = "gpt-5-mini"\n')
    codex.install()
    data = _toml(path)
    assert data["model"] == "gpt-5" and data["profiles"]["fast"]["model"] == "gpt-5-mini"
    assert data["notify"][-2:] == ["hook", "codex"]
    assert codex.status()["installed"] and codex.status()["current"]

    codex.install()  # idempotent
    assert path.read_text().count("notify =") == 1

    assert codex.uninstall()["removed"] == 1
    assert "notify" not in _toml(path)


def test_install_never_replaces_someone_elses_notify(isolated_home):
    path = codex.config_path()
    path.parent.mkdir(parents=True)
    original = 'notify = ["terminal-notifier", "-message", "done"]\n'
    path.write_text(original)
    with pytest.raises(ConfigError):
        codex.install()
    assert path.read_text() == original
    assert codex.status()["other_notify"]
    assert codex.uninstall()["removed"] == 0


def test_install_into_a_missing_config(isolated_home):
    codex.install()
    assert _toml(codex.config_path())["notify"][-1] == "codex"


def test_bom_and_crlf_survive(isolated_home):
    path = codex.config_path()
    path.parent.mkdir(parents=True)
    path.write_bytes('﻿model = "gpt-5"\r\n[tui]\r\nnotifications = true\r\n'.encode("utf-8"))
    codex.install()
    raw = path.read_bytes().decode("utf-8")
    assert raw.startswith('﻿notify = [') and "\r\n" in raw and "\n" not in raw.replace("\r\n", "")
    codex.uninstall()
    assert path.read_bytes().decode("utf-8") == '﻿model = "gpt-5"\r\n[tui]\r\nnotifications = true\r\n'


def test_a_bracket_inside_a_multiline_string_is_not_a_table(isolated_home):
    path = codex.config_path()
    path.parent.mkdir(parents=True)
    path.write_text('instructions = """\n[not a table]\n"""\nnotify = ["say", "done"]\n')
    with pytest.raises(ConfigError):  # that notify belongs to someone else
        codex.install()
    assert codex.status()["other_notify"]


def test_invalid_toml_is_left_alone(isolated_home):
    if sys.version_info < (3, 11):
        pytest.skip("tomllib needs Python 3.11+")
    path = codex.config_path()
    path.parent.mkdir(parents=True)
    path.write_text("model = \n")
    with pytest.raises(ConfigError):
        codex.install()
    assert path.read_text() == "model = \n"
