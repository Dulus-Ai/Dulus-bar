# -*- mode: python ; coding: utf-8 -*-
# Build:  pyinstaller --noconfirm DulusBar.spec
# Produces a windowed (no-console) Dulus Bar binary in dist/.
import sys

a = Analysis(
    ['dulus_bar/main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
    a.binaries,
    a.datas,
    [],
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
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# On macOS, wrap the binary in a proper .app bundle (LSUIElement so it has no
# Dock icon and behaves like a menu-bar / notch accessory).
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='DulusBar.app',
        icon=None,
        bundle_identifier='ai.dulus.bar',
        info_plist={
            'LSUIElement': True,
            'NSHighResolutionCapable': True,
        },
    )
