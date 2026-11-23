#!/usr/bin/env bash
# Package the project wheel for systems that get their native Qt bindings from Debian.
set -euo pipefail
cd "$(dirname "$0")/.."
python="${PYTHON:-python3}"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
"$python" -m pip wheel --no-deps --wheel-dir "$stage/wheels" .
wheel=("$stage"/wheels/dulus_bar-*-py3-none-any.whl)
root="$stage/package"
modules="$root/usr/lib/python3/dist-packages"
mkdir -p "$modules" "$root/DEBIAN" "$root/usr/bin" dist
"$python" -m zipfile -e "${wheel[0]}" "$modules"
version=$(PYTHONPATH="$modules" "$python" -c 'from importlib.metadata import version; print(version("dulus-bar"))')

cat > "$root/DEBIAN/control" <<EOF
Package: dulus-bar
Version: $version
Architecture: all
Maintainer: Dulus <dev@dulus.ai>
Depends: python3 (>= 3.10), python3-pyside6.qtwidgets (>= 6.4), qt6-qpa-plugins, python3-psutil (>= 5.9), python3-websockets (>= 12)
Description: Floating desktop island for local AI agents
EOF
cat > "$root/usr/bin/dulusbar" <<'PY'
#!/usr/bin/python3
from dulus_bar.main import main
raise SystemExit(main())
PY
chmod 755 "$root/usr/bin/dulusbar"
ln -s dulusbar "$root/usr/bin/dulus-bar"
install -Dm644 assets/dulusbar.desktop "$root/usr/share/applications/dulusbar.desktop"
install -Dm644 dulus_bar/assets/dulus-bird.png "$root/usr/share/icons/hicolor/256x256/apps/dulusbar.png"
install -Dm644 LICENSE "$root/usr/share/doc/dulus-bar/copyright"
dpkg-deb --root-owner-group --build "$root" "dist/dulus-bar_${version}_all.deb"
cp "${wheel[0]}" dist/
