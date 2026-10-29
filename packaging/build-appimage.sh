#!/usr/bin/env bash
# Builds dist/Zest-<version>-x86_64.AppImage with a bundled, relocatable Python and a bundled Lemonade.
# Needs: bash, curl, tar, sha256sum. Every download is pinned by checksum.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' zest/server.py)"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.12.15%2B20261013-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PY_SHA="178d3d83b6e8e99285a65d3fce9c16b87952cc5efdb4d1a0e3ff3be18ff0fc9c"
AIT_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
AIT_SHA="95cbe7cce9717fce90c484e34052ee7c7f1d7635b33c12525b4776826a7d29b6"   # "continuous" moves: refresh this when it does

# Lemonade's official "embeddable" build (Apache-2.0). To bundle a newer one, change these two lines.
LEM_VER="2026.43.3"
LEM_URL="https://github.com/lemonade-sdk/lemonade/releases/download/v${LEM_VER}/lemonade-embeddable-${LEM_VER}-ubuntu-x64.tar.gz"
LEM_SHA="d9a2fe01d7fd60ed9ec94822fea814a6d0e8aed46b39be8f126de74ba242b023"

# Cowork: Pi, the coding agent (MIT), run by a Node.js it ships with. Pi needs Node 22.19 or newer. npm checks these by
# their published sha512 integrity; Node by the checksum nodejs.org publishes.
NODE_VER="22.23.4"
NODE_URL="https://nodejs.org/dist/v${NODE_VER}/node-v${NODE_VER}-linux-x64.tar.xz"
NODE_SHA="1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce"
PI_URL="https://registry.npmjs.org/@earendil-works/pi-coding-agent/-/pi-coding-agent-1.4.0.tgz"
PI_INT="sha512-Z41LpZ3yYHSC/UUps6Mr39UMFPwH8elhJBG9DY1P7kuzsE5qSK/6eMj2EHzO5evJRQWLbKSqUMw+4nCNtIyiQg=="
JITI_URL="https://registry.npmjs.org/jiti/-/jiti-2.7.0.tgz"       # Pi loads extensions (Zest's approval gate) with it
JITI_INT="sha512-AC/7JofJvZGrrneWNaEnJeOLUx+JlGt7tNa0wZiRPT4MY1wmfKjt2+6O2p2uz2+skll8OZZmJMNqeke7kKbNgQ=="

CACHE=build/cache APPDIR=build/AppDir OUT=dist
mkdir -p "$CACHE" "$OUT"

fetch() {  # url sha256 dest
  if [ -f "$3" ] && echo "$2  $3" | sha256sum -c --status; then return; fi
  echo "Downloading $(basename "$3")"
  curl -fsSL "$1" -o "$3"
  echo "$2  $3" | sha256sum -c --quiet || { echo "Checksum mismatch for $3" >&2; exit 1; }
}
fetch512() {  # url sha512-integrity dest
  if [ -f "$3" ] && [ "sha512-$(openssl dgst -sha512 -binary "$3" | base64 -w0)" = "$2" ]; then return; fi
  echo "Downloading $(basename "$3")"
  curl -fsSL "$1" -o "$3"
  [ "sha512-$(openssl dgst -sha512 -binary "$3" | base64 -w0)" = "$2" ] || { echo "Integrity mismatch for $3" >&2; exit 1; }
}
fetch "$NODE_URL" "$NODE_SHA" "$CACHE/node.tar.xz"
fetch512 "$PI_URL" "$PI_INT" "$CACHE/pi.tgz"
fetch512 "$JITI_URL" "$JITI_INT" "$CACHE/jiti.tgz"
fetch "$PY_URL"  "$PY_SHA"  "$CACHE/python.tar.gz"
fetch "$AIT_URL" "$AIT_SHA" "$CACHE/appimagetool"
fetch "$LEM_URL" "$LEM_SHA" "$CACHE/lemonade.tar.gz"
chmod +x "$CACHE/appimagetool"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/python" "$APPDIR/usr/share/zest/pi" "$APPDIR/usr/lemonade" "$APPDIR/usr/node/bin" \
         "$APPDIR/usr/pi/node_modules/jiti"
tar -xzf "$CACHE/python.tar.gz" -C "$APPDIR/usr/python" --strip-components=1

# Zest only needs the standard library: drop what it never touches.
PYLIB="$APPDIR/usr/python/lib/python3.12"
rm -rf "$PYLIB"/{test,idlelib,tkinter,turtledemo,lib2to3,ensurepip,site-packages} \
       "$APPDIR/usr/python/lib"/{tcl*,tk*,itcl*,thread*} "$APPDIR/usr/python/share" "$APPDIR/usr/python/include"
find "$APPDIR" -name __pycache__ -prune -exec rm -rf {} +

./packaging/fetch-vendor.sh
cp zest/*.py zest/index.html zest/docs.md "$APPDIR/usr/share/zest/"
cp zest/pi/* "$APPDIR/usr/share/zest/pi/"
cp -r zest/skills zest/vendor zest/_vendor zest/providers "$APPDIR/usr/share/zest/"
# the sandboxed catalog test runs as another user: everything we ship must be world-readable
chmod -R a+rX "$APPDIR/usr/share/zest"

# Node (just the binary) and Pi (its self-contained bundle, plus jiti, which it loads extensions with).
tar -xJf "$CACHE/node.tar.xz" -C "$APPDIR/usr/node" --strip-components=1 "node-v${NODE_VER}-linux-x64/bin/node" "node-v${NODE_VER}-linux-x64/LICENSE"
tar -xzf "$CACHE/pi.tgz" -C "$APPDIR/usr/pi" --strip-components=1 package/package.json package/dist/bundle
tar -xzf "$CACHE/jiti.tgz" -C "$APPDIR/usr/pi/node_modules/jiti" --strip-components=1

# Lemonade: the server and its resources. Zest talks to it over HTTP, so the command-line tool isn't needed.
LEMTMP="$(mktemp -d)"
tar -xzf "$CACHE/lemonade.tar.gz" -C "$LEMTMP" --strip-components=1
cp -r "$LEMTMP/lemond" "$LEMTMP/resources" "$LEMTMP/LICENSE" "$APPDIR/usr/lemonade/"
rm -rf "$LEMTMP"
chmod +x "$APPDIR/usr/lemonade/lemond"
cp packaging/AppRun packaging/zest.desktop packaging/zest.png "$APPDIR/"
cp packaging/zest.png "$APPDIR/.DirIcon"

# Smoke test the bundled interpreter before packaging. (lemond itself needs glibc 2.38+, newer than some
# build machines, so it is only checked for presence here; Zest explains the requirement if it can't start.)
"$APPDIR/usr/python/bin/python3" -I "$APPDIR/usr/share/zest/server.py" --version
"$APPDIR/usr/node/bin/node" "$APPDIR/usr/pi/dist/bundle/cli.js" --version
[ -x "$APPDIR/usr/lemonade/lemond" ] && [ -d "$APPDIR/usr/lemonade/resources" ] || { echo "Lemonade is missing from the image" >&2; exit 1; }

FILE="$OUT/Zest-$VERSION-x86_64.AppImage"
ARCH=x86_64 "$CACHE/appimagetool" --appimage-extract-and-run --comp zstd "$APPDIR" "$FILE"
chmod +x "$FILE"
( cd "$OUT" && sha256sum "$(basename "$FILE")" > "$(basename "$FILE").sha256" )
echo "Built $FILE"
