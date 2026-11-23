import json
import os
import platform
import re
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conftest import free_port  # noqa: E402
from dulus_bar import macos_native  # noqa: E402


def test_native_binary_lives_in_the_cache_per_source_version(tmp_path):
    package = macos_native.package_root()
    binary = macos_native.native_binary(package)
    assert binary.parent.parent.parent == macos_native.cache_root()
    assert binary.parent.parent.name == f"native-{platform.machine()}-{macos_native.source_digest(package)}"
    assert binary.name == "DulusBarNative" and binary.parent.name == "release"


def test_native_binary_override(tmp_path):
    target = tmp_path / "custom-helper"
    with patch.dict(os.environ, {"DULUS_BAR_NATIVE_BINARY": str(target)}):
        assert macos_native.native_binary() == target.resolve()


def test_rosetta_and_arm_python_get_separate_builds(monkeypatch):
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    intel = macos_native.native_binary()
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert macos_native.native_binary() != intel


def test_universal_helper_uses_the_products_directory_swift_reports(tmp_path, monkeypatch):
    # A multi-arch build can use Xcode's apple/Products/Release layout.
    scratch = tmp_path / "swift build"
    products = scratch / "apple" / "Products" / "Release"
    products.mkdir(parents=True)
    helper = products / "DulusBarNative"
    helper.touch()
    package = macos_native.package_root()
    command = ["swift", "build", "-c", "release", "--package-path", str(package),
               "--scratch-path", str(scratch), "--arch", "x86_64", "--arch", "arm64"]
    monkeypatch.setattr(macos_native, "swift_toolchain", lambda: "swift")
    with (
        patch.object(macos_native.subprocess, "run") as build,
        patch.object(macos_native.subprocess, "check_output", return_value=f"{products}\n") as locate,
    ):
        assert macos_native.compile_native(package, scratch, ["x86_64", "arm64"]) == helper
    build.assert_called_once_with([*command, "--product", "DulusBarNative"], check=True)
    locate.assert_called_once_with([*command, "--show-bin-path"], text=True)


def test_frozen_app_uses_its_packaged_helper_before_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert macos_native.native_binary() == tmp_path / "DulusBarNative"
    with pytest.raises(FileNotFoundError, match="packaged native helper"):
        macos_native.run_native()
    override = tmp_path / "custom-helper"
    monkeypatch.setenv("DULUS_BAR_NATIVE_BINARY", str(override))
    assert macos_native.native_binary() == override


def test_a_source_change_means_a_new_build_but_finder_litter_does_not(tmp_path):
    package = tmp_path / "pkg"
    (package / "Sources" / "App").mkdir(parents=True)
    (package / "Package.swift").write_text("// swift-tools-version: 5.9\n")
    source = package / "Sources" / "App" / "main.swift"
    source.write_text("print(1)\n")
    first = macos_native.source_digest(package)
    (package / "Sources" / "App" / ".DS_Store").write_bytes(b"\0\1")
    assert macos_native.source_digest(package) == first
    source.write_text("print(2)\n")
    assert macos_native.source_digest(package) != first


def test_the_whole_swift_package_ships_in_the_wheel():
    """The Swift package is package data: a file the globs miss would break
    the native build of every pip install (even a missing test folder)."""
    tomllib = pytest.importorskip("tomllib")
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["dulus_bar"]
    package_dir, swift = ROOT / "dulus_bar", macos_native.package_root()
    files = [p for p in swift.rglob("*")
             if p.is_file() and not any(part.startswith(".") for part in p.relative_to(swift).parts)]
    assert any(p.parent.name == "DulusBarNativeTests" for p in files)
    for path in files:
        rel = path.relative_to(package_dir)
        assert any(rel.match(pattern) for pattern in patterns), f"{rel} is not in package-data"


def test_only_builds_nobody_started_for_a_month_are_pruned(tmp_path):
    """The cache is shared by every install (a checkout and a pip install, two
    venvs): a fresh build must not delete the build another one runs."""
    import time

    cache = tmp_path / "cache"
    current = cache / "native-0123456789abcdef"
    recent, stale = cache / "native-1111111111111111", cache / "native-fedcba9876543210"
    unrelated = cache / "native-notes"
    for path in (current, recent, stale, unrelated):
        path.mkdir(parents=True)
    old = time.time() - 40 * 24 * 3600
    for path in (stale, unrelated):
        os.utime(path, (old, old))
    macos_native._prune_builds(current)
    assert current.exists() and recent.exists() and unrelated.exists() and not stale.exists()
    os.utime(recent, (old, old))
    macos_native._mark_used(recent)  # started again: it's in use
    macos_native._prune_builds(current)
    assert recent.exists()


def test_no_command_line_tools_means_no_swift(monkeypatch):
    """Without the Xcode CLT, /usr/bin/swift is a stub that opens an installer
    dialog: never run it."""
    from types import SimpleNamespace

    monkeypatch.setattr(macos_native.shutil, "which", lambda name: "/usr/bin/swift")
    monkeypatch.setattr(macos_native.sys, "platform", "darwin")
    monkeypatch.setattr(macos_native.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=2))
    assert macos_native.swift_toolchain() is None
    with pytest.raises(FileNotFoundError, match="xcode-select --install"):
        macos_native.build_native()
    monkeypatch.setattr(macos_native.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    assert macos_native.swift_toolchain() == "/usr/bin/swift"


def _fake_helper(tmp_path, body):
    helper = tmp_path / "helper.sh"
    helper.write_text("#!/bin/sh\n" + body + "\n")
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    return helper


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell helper")
def test_a_helper_that_crashes_at_launch_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("DULUS_BAR_PORT", str(free_port()))
    monkeypatch.setenv("DULUS_BAR_NATIVE_BINARY", str(_fake_helper(tmp_path, "exit 3")))
    with pytest.raises(RuntimeError, match="exited at launch"):
        macos_native.run_native()
    # ...and the hub's port is free again for the Qt island.
    from dulus_bar.server import AgentEventServer

    hub = AgentEventServer(port=int(os.environ["DULUS_BAR_PORT"]))
    assert hub.start()
    hub.stop()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell helper")
def test_the_helper_learns_how_to_open_wired_agents(tmp_path, monkeypatch):
    seen = tmp_path / "seen.json"
    script = (f"{sys.executable} -c 'import json, os, sys; json.dump("
              f"{{\"env\": dict(os.environ), \"argv\": sys.argv[1:]}}, open(\"{seen}\", \"w\"))' \"$@\"")
    monkeypatch.setenv("DULUS_BAR_PORT", str(free_port()))
    monkeypatch.setenv("DULUS_BAR_NATIVE_BINARY", str(_fake_helper(tmp_path, script)))
    assert macos_native.run_native() == 0  # a helper that quits normally
    seen = json.loads(seen.read_text())
    env, argv = seen["env"], seen["argv"]
    assert argv == ["--websocket", f"ws://127.0.0.1:{os.environ['DULUS_BAR_PORT']}"]
    assert env["DULUS_BAR_TOKEN"] and env["DULUS_BAR_PARENT_PID"] == str(os.getpid())
    assert not any(env["DULUS_BAR_TOKEN"] in arg for arg in argv)  # never visible in `ps`
    cli = json.loads(env["DULUS_BAR_CLI"])
    assert cli[-1].endswith("main.py")  # `<python> …/dulus_bar/main.py`, i.e. `dulusbar`
    assert json.loads(env["DULUS_BAR_OPEN_DULUS"])[-1].endswith("dulus_wrapper.py")


def test_should_use_native_platform_and_override():
    with patch.object(sys, "platform", "darwin"), patch.dict(os.environ, {}, clear=True):
        assert macos_native.should_use_native()
    with patch.object(sys, "platform", "darwin"), patch.dict(
        os.environ, {"DULUS_BAR_FORCE_QT": "1"}, clear=True
    ):
        assert not macos_native.should_use_native()
    with patch.object(sys, "platform", "linux"), patch.dict(os.environ, {}, clear=True):
        assert not macos_native.should_use_native()


def test_qt_fallback_opens_agents_through_a_script_file(monkeypatch, tmp_path):
    """The terminal command goes in a script file: inlined into AppleScript,
    any quote in a path or argument made the terminal open and close at once."""
    import tempfile
    from types import SimpleNamespace

    from dulus_bar.native import macos

    calls = []
    monkeypatch.setattr(macos.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    assert macos.MacOSBackend().open_terminal_running(["claude", 'say "hi"'], title="Claude", cwd="/tmp/my dir")
    script = calls[0][2]
    path = re.search(r"do script \"bash '?([^'\"]+)'?\"", script).group(1)
    body = Path(path).read_text()
    assert "cd '/tmp/my dir' && claude 'say \"hi\"'" in body


@pytest.mark.skipif(os.name == "nt", reason="runs the generated bash script")
def test_a_file_name_with_quotes_still_opens_the_agent(monkeypatch, tmp_path):
    """The terminal title is usually the agent's file name: "kev's agent"
    must neither break the script nor run as shell code."""
    import subprocess
    import tempfile
    from types import SimpleNamespace

    from dulus_bar.native import macos

    real_run, calls = subprocess.run, []
    monkeypatch.setattr(macos.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    marker = tmp_path / "pwned"
    title = f"kev's agent'; touch {marker}; echo '"
    assert macos.MacOSBackend().open_terminal_running(["echo", "agent ran"], title=title, cwd=str(tmp_path))
    path = re.search(r"do script \"bash '?([^'\"]+)'?\"", calls[0][2]).group(1)
    ran = real_run(["bash", path], capture_output=True, text=True, timeout=10)
    assert ran.returncode == 0 and "agent ran" in ran.stdout
    assert not marker.exists()
