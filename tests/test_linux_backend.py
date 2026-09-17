"""Linux backend: terminal spawning, window activation and overlay placement.

Everything here is Qt-free on purpose so it runs headless, which is exactly
where the Linux bugs these tests cover used to hide.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dulus_bar.native import linux as lx


class FakeProc:
    """subprocess.Popen stand-in: either exits with `code` or keeps running."""

    def __init__(self, code=None):
        self._code = code
        self.returncode = code

    def wait(self, timeout=None):
        if self._code is None:
            raise subprocess.TimeoutExpired(cmd="term", timeout=timeout)
        self.returncode = self._code
        return self._code


# --- command style per emulator ------------------------------------------


def test_terminal_argv_uses_the_flag_each_emulator_actually_supports():
    script = "agent; exec /bin/bash"
    # gnome-terminal dropped -e upstream: it must be `--`.
    assert lx._terminal_argv("gnome-terminal", lx._DASHDASH, "/bin/bash", script) == [
        "gnome-terminal", "--", "/bin/bash", "-c", script,
    ]
    assert lx._terminal_argv("konsole", lx._DASHE, "/bin/bash", script) == [
        "konsole", "-e", "/bin/bash", "-c", script,
    ]
    # kitty/foot take the command with no flag at all.
    assert lx._terminal_argv("kitty", lx._DIRECT, "/bin/bash", script) == [
        "kitty", "/bin/bash", "-c", script,
    ]
    assert lx._terminal_argv("wezterm", lx._WEZTERM, "/bin/bash", script) == [
        "wezterm", "start", "--", "/bin/bash", "-c", script,
    ]
    # xfce4-terminal/tilix want ONE quoted string.
    assert lx._terminal_argv("xfce4-terminal", lx._COMMAND, "/bin/bash", script) == [
        "xfce4-terminal", "--command", "/bin/bash -c 'agent; exec /bin/bash'",
    ]


def test_style_lookup_resolves_debian_alternatives_link(tmp_path):
    link = tmp_path / "x-terminal-emulator"
    target = tmp_path / "gnome-terminal"
    target.write_text("#!/bin/sh\n")
    link.symlink_to(target)
    with patch.object(lx.shutil, "which", return_value=str(link)):
        assert lx._style_for_exe("x-terminal-emulator") == lx._DASHDASH


def test_style_lookup_falls_back_to_dash_e_for_unknown_terminals():
    with patch.object(lx.shutil, "which", return_value=None):
        assert lx._style_for_exe("some-exotic-term") == lx._DASHE


def test_preferred_terminals_honours_TERMINAL_and_the_desktop():
    with patch.dict(os.environ, {"TERMINAL": "foot", "XDG_CURRENT_DESKTOP": "KDE"}, clear=True):
        preferred = lx._preferred_terminals()
    assert preferred[0] == "foot"
    assert "konsole" in preferred
    assert "x-terminal-emulator" in preferred


# --- spawning: a terminal that rejects our flags must not count as success ---


def test_spawn_rejects_a_terminal_that_dies_on_its_arguments():
    with patch.object(lx.subprocess, "Popen", return_value=FakeProc(code=1)):
        assert lx.LinuxBackend._spawn(["gnome-terminal", "-e", "sh"], None) is False


def test_spawn_accepts_a_terminal_that_stays_alive_or_hands_off():
    with patch.object(lx.subprocess, "Popen", return_value=FakeProc(code=None)):
        assert lx.LinuxBackend._spawn(["xterm", "-e", "sh"], None) is True
    # gnome-terminal & friends exit 0 immediately after handing off to a server.
    with patch.object(lx.subprocess, "Popen", return_value=FakeProc(code=0)):
        assert lx.LinuxBackend._spawn(["gnome-terminal", "--", "sh"], None) is True


def test_open_terminal_running_falls_through_to_a_working_emulator():
    attempts = []

    def fake_popen(argv, **kwargs):
        attempts.append(argv)
        return FakeProc(code=1 if argv[0] == "gnome-terminal" else None)

    with patch.object(
        lx, "terminal_candidates", return_value=[("gnome-terminal", lx._DASHDASH), ("xterm", lx._DASHE)]
    ), patch.object(lx.subprocess, "Popen", side_effect=fake_popen):
        assert lx.LinuxBackend().open_terminal_running(["claude", "--help"], title="Claude Code") is True

    assert [argv[0] for argv in attempts] == ["gnome-terminal", "xterm"]
    script = attempts[-1][-1]
    assert "claude --help" in script
    assert script.startswith("printf '\\033]0;Claude Code\\007';")  # window title for jump-to-terminal
    assert "; exec " in script  # terminal stays open after the agent exits


def test_open_terminal_running_reports_failure_when_nothing_works():
    with patch.object(lx, "terminal_candidates", return_value=[("xterm", lx._DASHE)]), patch.object(
        lx.subprocess, "Popen", side_effect=OSError("no display")
    ):
        assert lx.LinuxBackend().open_terminal_running(["claude"]) is False


def test_title_escape_cannot_break_out_of_the_osc_sequence():
    assert lx._title_escape("Claude'\x07 Code\\") == "Claude Code"


# --- window activation ----------------------------------------------------


def test_activate_window_converts_wmctrl_hex_ids_for_xdotool():
    backend = lx.LinuxBackend()
    backend._wmctrl = None
    backend._xdotool = "/usr/bin/xdotool"
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    with patch.object(lx.subprocess, "run", side_effect=fake_run):
        assert backend.activate_window("0x0400000a") is True
    assert calls[0] == ["/usr/bin/xdotool", "windowactivate", "--sync", str(0x0400000A)]


def test_list_windows_falls_back_to_xdotool_without_wmctrl():
    backend = lx.LinuxBackend()
    backend._wmctrl = None
    backend._xdotool = "/usr/bin/xdotool"
    outputs = {
        "search": "12345\n67890\n",
        "getwindowname": "Claude Code\nFirefox\n",
        "getwindowpid": "111\n222\n",
    }

    def fake_run(argv, **kwargs):
        key = next(k for k in outputs if k in argv)
        return subprocess.CompletedProcess(argv, 0, stdout=outputs[key])

    with patch.object(lx.subprocess, "run", side_effect=fake_run), patch.object(
        lx, "_proc_name", return_value="gnome-terminal"
    ):
        windows = backend.list_windows()
        found = backend.find_window_by_title("claude")

    assert [w.title for w in windows] == ["Claude Code", "Firefox"]
    assert [w.pid for w in windows] == [111, 222]
    assert found is not None and found.handle == "12345"


# --- session detection & overlay placement --------------------------------


def test_session_type_prefers_xdg_then_falls_back_to_sockets():
    with patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True):
        assert lx.session_type() == "wayland" and lx.is_wayland()
    with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True):
        assert lx.session_type() == "wayland"
    with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
        assert lx.session_type() == "x11" and not lx.is_wayland()
    with patch.dict(os.environ, {}, clear=True):
        assert lx.session_type() == "unknown"


def test_prepare_environment_switches_wayland_to_xwayland():
    env = {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}
    with patch.dict(os.environ, env, clear=True):
        lx.LinuxBackend().prepare_environment()
        assert os.environ["QT_QPA_PLATFORM"] == "xcb"


def test_prepare_environment_leaves_x11_and_explicit_choices_alone():
    with patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, clear=True):
        lx.LinuxBackend().prepare_environment()
        assert "QT_QPA_PLATFORM" not in os.environ
    # User opted into native Wayland, or picked a platform plugin themselves.
    for extra in ({"DULUS_BAR_KEEP_WAYLAND": "1"}, {"QT_QPA_PLATFORM": "wayland"}):
        env = {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0", **extra}
        with patch.dict(os.environ, env, clear=True):
            lx.LinuxBackend().prepare_environment()
            assert os.environ.get("QT_QPA_PLATFORM") in (None, "wayland")
    # No X server to fall back to: nothing we can do, but don't break Qt.
    with patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True):
        lx.LinuxBackend().prepare_environment()
        assert "QT_QPA_PLATFORM" not in os.environ


def test_overlay_bypasses_window_manager_placement_on_x11():
    backend = lx.LinuxBackend()
    with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
        assert backend.overlay_extra_window_flags() == ("X11BypassWindowManagerHint",)
    # Escape hatch: let the WM manage the island again.
    with patch.dict(os.environ, {"DISPLAY": ":0", "DULUS_BAR_X11_WM": "1"}, clear=True):
        assert backend.overlay_extra_window_flags() == ()
    with patch.dict(os.environ, {}, clear=True):
        assert backend.overlay_extra_window_flags() == ()


def test_overlay_anchors_inside_the_work_area_by_default():
    backend = lx.LinuxBackend()
    with patch.dict(os.environ, {}, clear=True):
        assert backend.overlay_use_available_geometry() is True
    with patch.dict(os.environ, {"DULUS_BAR_FULL_SCREEN_EDGE": "1"}, clear=True):
        assert backend.overlay_use_available_geometry() is False
    # Re-assert placement more than once: some WMs move the window late.
    assert len(backend.overlay_settle_delays_ms()) > 2
