import json
import sys

import pytest

from dulus_bar import autostart
from dulus_bar.config import DEFAULTS, Settings


def test_settings_persist_and_ignore_junk(tmp_path):
    path = tmp_path / "config.json"
    settings = Settings(path)
    assert settings["autohide"] is True
    settings.set("do_not_disturb", True)
    assert Settings(path)["do_not_disturb"] is True
    path.write_text(json.dumps({"autohide": False, "bogus": 1}))
    assert Settings(path)["autohide"] is False and Settings(path)["bogus"] is None
    path.write_text("{broken")
    assert Settings(path)["notifications"] == DEFAULTS["notifications"]
    assert Settings(path).toggle("notifications") is (not DEFAULTS["notifications"])


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="XDG autostart is Linux-only")
def test_linux_start_at_login(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(autostart, "_argv", lambda: ["/opt/my python/python3", "/x/main.py", "run"])
    assert not autostart.is_enabled()
    path = autostart.enable()
    assert autostart.is_enabled()
    text = open(path, encoding="utf-8").read()
    assert 'Exec="/opt/my python/python3" /x/main.py run' in text
    autostart.disable()
    assert not autostart.is_enabled()


def test_desktop_quoting():
    assert autostart._desktop_quote("plain") == "plain"
    assert autostart._desktop_quote('a "b" $c') == '"a \\"b\\" \\$c"'
