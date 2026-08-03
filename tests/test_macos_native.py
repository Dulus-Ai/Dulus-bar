import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dulus_bar import macos_native


def test_native_binary_defaults_to_release(tmp_path):
    assert macos_native.native_binary(tmp_path) == (
        tmp_path / "macos" / ".build" / "release" / "DulusBarNative"
    )


def test_native_binary_override(tmp_path):
    target = tmp_path / "custom-helper"
    with patch.dict(os.environ, {"DULUS_BAR_NATIVE_BINARY": str(target)}):
        assert macos_native.native_binary() == target.resolve()


def test_should_use_native_platform_and_override():
    with patch.object(sys, "platform", "darwin"), patch.dict(os.environ, {}, clear=True):
        assert macos_native.should_use_native()
    with patch.object(sys, "platform", "darwin"), patch.dict(
        os.environ, {"DULUS_BAR_FORCE_QT": "1"}, clear=True
    ):
        assert not macos_native.should_use_native()
    with patch.object(sys, "platform", "linux"), patch.dict(os.environ, {}, clear=True):
        assert not macos_native.should_use_native()
