#!/usr/bin/env bash
# Builds dist/Shoggoth-<version>-x86_64.AppImage with a bundled, relocatable Python and a bundled Lemonade.
# Needs: bash, curl, tar, sha256sum. Every download is pinned by checksum.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' shoggoth/server.py)"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.12.15%2B20261013-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PY_SHA="178d3d83b6e8e99285a65d3fce9c16b87952cc5efdb4d1a0e3ff3be18ff0fc9c"
AIT_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
AIT_SHA="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"   # a numbered release: "continuous" is rebuilt and breaks the pin

# Lemonade's official "embeddable" build (Apache-2.0). To bundle a newer one, change these two lines.
LEM_VER="2026.43.3"
LEM_URL="https://github.com/lemonade-sdk/lemonade/releases/download/v${LEM_VER}/lemonade-embeddable-${LEM_VER}-ubuntu-x64.tar.gz"
LEM_SHA="d9a2fe01d7fd60ed9ec94822fea814a6d0e8aed46b39be8f126de74ba242b023"

# Lemonade needs glibc 2.38; older systems run it through this glibc (Ubuntu 24.04's), shipped inside the image.
# Each .deb is pinned by checksum, as is everything else here.
UB="https://archive.ubuntu.com/ubuntu/pool/main"
GLIBC_DEBS="
$UB/g/glibc/libc6_2.39-0ubuntu8.9_amd64.deb ff5557d99b51f761c4b7c92368b9cc45565eda17df9bf9eb4b134d09825008be
$UB/g/gcc-14/libstdc++6_14.2.0-4ubuntu2~24.04.1_amd64.deb a51f8de7829211db961a31f02158058ad1a95f92ac6d0a5dff6350e2821c54c0
$UB/g/gcc-14/libgcc-s1_14.2.0-4ubuntu2~24.04.1_amd64.deb aa7fadbe33b78bcf99885318040601c550c208929565b179891d9a3cc2aa68cd
$UB/o/openssl/libssl3t64_3.0.13-0ubuntu3.16_amd64.deb 219f43b1cd836a4da550938db5fda93160d269d39a4edcf1f1ce698a470db797
$UB/libz/zstd/libzstd1_1.5.5+dfsg2-2build1.1_amd64.deb dfcf25061e07aad7efd3f4f880ba5ad4d4d09ebe7fc8cc77ab6b8a161d6d4727
$UB/z/zlib/zlib1g_1.3.dfsg-3.1ubuntu2.3_amd64.deb 376249c8de139568221dbb242318cc8cb8f02299e3a62b7432948e1754fbddf9
$UB/libd/libdrm/libdrm2_2.4.125-1ubuntu0.1~24.04.2_amd64.deb 4d3e858dd57be617c49d87c5ddcdb35df744a4f0a559981256ae2ba174718584
$UB/libd/libdrm/libdrm-amdgpu1_2.4.125-1ubuntu0.1~24.04.2_amd64.deb aa29b7898bae596bf07e55ba178cf8d5ea698fe83688e1b34520d86010251a7a
"

# Cowork: Pi, the coding agent (MIT), run by a Node.js it ships with. Pi needs Node 22.19 or newer. npm checks these by
# their published sha512 integrity; Node by the checksum nodejs.org publishes.
NODE_VER="22.23.4"
NODE_URL="https://nodejs.org/dist/v${NODE_VER}/node-v${NODE_VER}-linux-x64.tar.xz"
NODE_SHA="1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce"
PI_URL="https://registry.npmjs.org/@earendil-works/pi-coding-agent/-/pi-coding-agent-1.4.0.tgz"
PI_INT="sha512-Z41LpZ3yYHSC/UUps6Mr39UMFPwH8elhJBG9DY1P7kuzsE5qSK/6eMj2EHzO5evJRQWLbKSqUMw+4nCNtIyiQg=="
JITI_URL="https://registry.npmjs.org/jiti/-/jiti-2.7.0.tgz"       # Pi loads extensions (Shoggoth's approval gate) with it
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
echo "$GLIBC_DEBS" | while read -r url sha; do [ -n "$url" ] && fetch "$url" "$sha" "$CACHE/$(basename "$url")"; done

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/python" "$APPDIR/usr/share/shoggoth/pi" "$APPDIR/usr/lemonade" "$APPDIR/usr/node/bin" \
         "$APPDIR/usr/pi/node_modules/jiti"
tar -xzf "$CACHE/python.tar.gz" -C "$APPDIR/usr/python" --strip-components=1

# Shoggoth only needs the standard library: drop what it never touches.
PYLIB="$APPDIR/usr/python/lib/python3.12"
rm -rf "$PYLIB"/{test,idlelib,tkinter,turtledemo,lib2to3,ensurepip,site-packages} \
       "$APPDIR/usr/python/lib"/{tcl*,tk*,itcl*,thread*} "$APPDIR/usr/python/share" "$APPDIR/usr/python/include"
find "$APPDIR" -name __pycache__ -prune -exec rm -rf {} +

./packaging/fetch-vendor.sh
cp shoggoth/*.py shoggoth/index.html shoggoth/docs.md "$APPDIR/usr/share/shoggoth/"
cp shoggoth/pi/* "$APPDIR/usr/share/shoggoth/pi/"
cp -r shoggoth/skills shoggoth/vendor shoggoth/_vendor shoggoth/providers shoggoth/recommendations "$APPDIR/usr/share/shoggoth/"
mkdir -p "$APPDIR/usr/share/shoggoth/fonts" && cp docs/plans/assets/fonts/OFL-*.txt "$APPDIR/usr/share/shoggoth/fonts/"   # the fonts embedded in index.html
# the sandboxed catalog test runs as another user: everything we ship must be world-readable
chmod -R a+rX "$APPDIR/usr/share/shoggoth"

# Node (just the binary) and Pi (its self-contained bundle, plus jiti, which it loads extensions with).
tar -xJf "$CACHE/node.tar.xz" -C "$APPDIR/usr/node" --strip-components=1 "node-v${NODE_VER}-linux-x64/bin/node" "node-v${NODE_VER}-linux-x64/LICENSE"
tar -xzf "$CACHE/pi.tgz" -C "$APPDIR/usr/pi" --strip-components=1 package/package.json package/dist/bundle
tar -xzf "$CACHE/jiti.tgz" -C "$APPDIR/usr/pi/node_modules/jiti" --strip-components=1

# Lemonade: the server and its resources. Shoggoth talks to it over HTTP, so the command-line tool isn't needed.
LEMTMP="$(mktemp -d)"
tar -xzf "$CACHE/lemonade.tar.gz" -C "$LEMTMP" --strip-components=1
cp -r "$LEMTMP/lemond" "$LEMTMP/resources" "$LEMTMP/LICENSE" "$APPDIR/usr/lemonade/"
rm -rf "$LEMTMP"
chmod +x "$APPDIR/usr/lemonade/lemond"
# The glibc and the libraries Lemonade links against, flat in one folder: the loader is pointed at it (see engine.py).
GL="$APPDIR/usr/glibc"; GLTMP="$(mktemp -d)"; mkdir -p "$GL"
echo "$GLIBC_DEBS" | while read -r url sha; do [ -n "$url" ] && dpkg-deb -x "$CACHE/$(basename "$url")" "$GLTMP"; done
cp -a "$GLTMP"/usr/lib/x86_64-linux-gnu/*.so* "$GLTMP"/lib/x86_64-linux-gnu/*.so* "$GL/" 2>/dev/null || true
cp -a "$GLTMP"/usr/lib64/ld-linux-x86-64.so.2 "$GLTMP"/lib64/ld-linux-x86-64.so.2 "$GL/" 2>/dev/null || true
rm -rf "$GLTMP"
[ -e "$GL/ld-linux-x86-64.so.2" ] && [ -e "$GL/libc.so.6" ] || { echo "glibc is missing from the image" >&2; exit 1; }
cp packaging/AppRun packaging/shoggoth.desktop packaging/shoggoth.png "$APPDIR/"
cp packaging/shoggoth.png "$APPDIR/.DirIcon"

# Smoke test the bundled interpreter before packaging. (lemond itself needs glibc 2.38+, so it is run through the
# bundled glibc, which is also what an older system does.)
"$GL/ld-linux-x86-64.so.2" --library-path "$GL" "$APPDIR/usr/lemonade/lemond" --version
"$APPDIR/usr/python/bin/python3" -I "$APPDIR/usr/share/shoggoth/server.py" --version
"$APPDIR/usr/node/bin/node" "$APPDIR/usr/pi/dist/bundle/cli.js" --version
[ -x "$APPDIR/usr/lemonade/lemond" ] && [ -d "$APPDIR/usr/lemonade/resources" ] || { echo "Lemonade is missing from the image" >&2; exit 1; }

FILE="$OUT/Shoggoth-$VERSION-x86_64.AppImage"
ARCH=x86_64 "$CACHE/appimagetool" --appimage-extract-and-run --comp zstd "$APPDIR" "$FILE"
chmod +x "$FILE"
( cd "$OUT" && sha256sum "$(basename "$FILE")" > "$(basename "$FILE").sha256" )
echo "Built $FILE"
