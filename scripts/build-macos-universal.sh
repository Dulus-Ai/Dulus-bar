#!/usr/bin/env bash
# Run with the universal2 installer from python.org and Xcode.
set -euo pipefail
cd "$(dirname "$0")/.."
python="${PYTHON:-python3}"
lipo "$("$python" -c 'import sys; print(sys.executable)')" -verify_arch x86_64 arm64

temp="$(mktemp -d)"
trap 'rm -rf "$temp"' EXIT
"$python" -m venv "$temp/venv"
python="$temp/venv/bin/python"
wheels="$temp/wheels"
export MACOSX_DEPLOYMENT_TARGET=13.0
export ARCHFLAGS="-arch x86_64 -arch arm64"

# These extensions also ship thin wheels. Build both slices, then resolve
# the remaining wheels, including the Qt and ObjC bindings, for Ventura universal2.
"$python" -m pip wheel --no-cache-dir --no-deps --no-binary=psutil,websockets -w "$wheels" psutil websockets .
app_wheel=("$wheels"/dulus_bar-*.whl)
"$python" -m pip download --only-binary=:all: --platform macosx_13_0_universal2 \
    --find-links "$wheels" -d "$wheels" "${app_wheel[0]}[build]"
"$python" -m pip install --no-index --find-links "$wheels" "${app_wheel[0]}[build]"

DULUS_BAR_MACOS_ARCH=universal2 "$python" -m PyInstaller --clean --noconfirm DulusBar.spec
app=dist/DulusBar.app
while IFS= read -r -d '' path; do
    if [[ "$(file -b "$path")" == *Mach-O* ]]; then
        lipo "$path" -verify_arch x86_64 arm64
    fi
done < <(find "$app" -type f -print0)
codesign --verify --deep --strict "$app"
ditto -c -k --sequesterRsrc --keepParent "$app" dist/DulusBar-macos-universal2.zip
