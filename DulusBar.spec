# -*- mode: python ; coding: utf-8 -*-
# Build:  pyinstaller --noconfirm DulusBar.spec
# Produces a windowed (no-console) Dulus Bar binary in dist/. The binary is
# also the CLI: `DulusBar hooks install`, `DulusBar status`, … (hooks and
# launched agents re-invoke it, so no separate Python is needed).
import os
import platform
import subprocess
import sys
from pathlib import Path

# The bird: tray icon, window icon and the island's brand mark.
datas = [('dulus_bar/assets/dulus-bird.png', 'dulus_bar/assets')]
binaries = []
mac_arch = None
if sys.platform == 'darwin':
    sys.path.insert(0, SPECPATH)
    from dulus_bar import __version__
    from dulus_bar.macos_native import compile_native

    mac_arch = os.environ.get('DULUS_BAR_MACOS_ARCH', platform.machine())
    archs = {'x86_64': ['x86_64'], 'arm64': ['arm64'], 'universal2': ['x86_64', 'arm64']}[mac_arch]
    helper = compile_native(Path(SPECPATH) / 'dulus_bar/macos', Path(workpath) / 'swift', archs)
    subprocess.run(['lipo', str(helper), '-verify_arch', *archs], check=True)
    binaries.append((str(helper), '.'))
    resources = helper.with_name('DulusBarNative_DulusBarNative.bundle')
    datas.append((str(resources), resources.name))

a = Analysis(
    ['dulus_bar/main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    # Imported lazily (per platform / per command), so list them explicitly.
    hiddenimports=[
        'dulus_bar.overlay',
        'dulus_bar.macos_native',
        'dulus_bar.doctor',
        'dulus_bar.integrations.claude',
        'dulus_bar.integrations.codex',
        'dulus_bar.wrappers.base_wrapper',
        'dulus_bar.native.linux',
        'dulus_bar.native.macos',
        'dulus_bar.native.windows',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [] if mac_arch else a.binaries + a.datas,
    [],
    exclude_binaries=bool(mac_arch),
    name='DulusBar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=mac_arch,
    codesign_identity=None,
    entitlements_file=None,
)

# On macOS, wrap the binary in a proper .app bundle (LSUIElement so it has no
# Dock icon and behaves like a menu-bar / notch accessory).
if sys.platform == 'darwin':
    # Keep native code on disk inside the .app so the embedding app can sign it.
    collection = COLLECT(exe, a.binaries, a.datas, name='DulusBar')
    app = BUNDLE(
        collection,
        name='DulusBar.app',
        icon=None,
        bundle_identifier='ai.dulus.bar',
        version=__version__,
        info_plist={
            'CFBundleVersion': __version__,
            'LSUIElement': True,
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '13.0',
        },
    )
