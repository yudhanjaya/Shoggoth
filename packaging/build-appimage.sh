#!/usr/bin/env bash
# Builds dist/Zest-<version>-x86_64.AppImage with a bundled, relocatable Python.
# Needs: bash, curl, tar, sha256sum. Downloads are pinned by checksum.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' zest/server.py)"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.12.15%2B20261013-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PY_SHA="178d3d83b6e8e99285a65d3fce9c16b87952cc5efdb4d1a0e3ff3be18ff0fc9c"
AIT_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
AIT_SHA="95cbe7cce9717fce90c484e34052ee7c7f1d7635b33c12525b4776826a7d29b6"   # "continuous" moves: refresh this when it does

CACHE=build/cache APPDIR=build/AppDir OUT=dist
mkdir -p "$CACHE" "$OUT"

fetch() {  # url sha256 dest
  if [ -f "$3" ] && echo "$2  $3" | sha256sum -c --status; then return; fi
  echo "Downloading $(basename "$3")"
  curl -fsSL "$1" -o "$3"
  echo "$2  $3" | sha256sum -c --quiet || { echo "Checksum mismatch for $3" >&2; exit 1; }
}
fetch "$PY_URL"  "$PY_SHA"  "$CACHE/python.tar.gz"
fetch "$AIT_URL" "$AIT_SHA" "$CACHE/appimagetool"
chmod +x "$CACHE/appimagetool"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/python" "$APPDIR/usr/share/zest"
tar -xzf "$CACHE/python.tar.gz" -C "$APPDIR/usr/python" --strip-components=1

# Zest only needs the standard library: drop what it never touches.
PYLIB="$APPDIR/usr/python/lib/python3.12"
rm -rf "$PYLIB"/{test,idlelib,tkinter,turtledemo,lib2to3,ensurepip,site-packages} \
       "$APPDIR/usr/python/lib"/{tcl*,tk*,itcl*,thread*} "$APPDIR/usr/python/share" "$APPDIR/usr/python/include"
find "$APPDIR" -name __pycache__ -prune -exec rm -rf {} +

cp zest/server.py zest/index.html "$APPDIR/usr/share/zest/"
cp packaging/AppRun packaging/zest.desktop packaging/zest.png "$APPDIR/"
cp packaging/zest.png "$APPDIR/.DirIcon"

# Smoke test the bundled interpreter before packaging.
"$APPDIR/usr/python/bin/python3" -I "$APPDIR/usr/share/zest/server.py" --version

FILE="$OUT/Zest-$VERSION-x86_64.AppImage"
ARCH=x86_64 "$CACHE/appimagetool" --appimage-extract-and-run --comp zstd "$APPDIR" "$FILE"
chmod +x "$FILE"
( cd "$OUT" && sha256sum "$(basename "$FILE")" > "$(basename "$FILE").sha256" )
echo "Built $FILE"
