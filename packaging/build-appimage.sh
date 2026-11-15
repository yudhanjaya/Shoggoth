#!/usr/bin/env bash
# Builds dist/Shoggoth-<version>-x86_64.AppImage with a bundled, relocatable Python and a processor-only llama.cpp.
# Needs: bash, curl, tar, sha256sum. Every download is pinned by checksum.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' shoggoth/server.py)"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.12.15%2B20261013-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PY_SHA="178d3d83b6e8e99285a65d3fce9c16b87952cc5efdb4d1a0e3ff3be18ff0fc9c"
AIT_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
AIT_SHA="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"   # a numbered release: "continuous" is rebuilt and breaks the pin

# llama.cpp's official processor-only build (MIT), so Shoggoth runs models offline on first run. Shoggoth downloads the build for the
# graphics card itself (llamacpp.py). The checksum is the `digest` GitHub publishes for the asset. The ubuntu builds need glibc 2.38
# (Ubuntu 24.04, Debian 13, Fedora 39 or Linux Mint 22 and newer). To bundle a newer one, change these two lines.
LLAMA_TAG="b12375"
LLAMA_URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_TAG}/llama-${LLAMA_TAG}-bin-ubuntu-x64.tar.gz"
LLAMA_SHA="89fdf5a4066170fa408a5ebe1510b50bd8a5bab750e49ae531bb1d4cdbae65e3"

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
fetch "$LLAMA_URL" "$LLAMA_SHA" "$CACHE/llama-cpu.tar.gz"
chmod +x "$CACHE/appimagetool"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/python" "$APPDIR/usr/share/shoggoth/pi" "$APPDIR/usr/llamacpp/$LLAMA_TAG/cpu" "$APPDIR/usr/node/bin" \
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
cp -r shoggoth/skills shoggoth/vendor shoggoth/_vendor shoggoth/providers shoggoth/recommendations shoggoth/engines.d "$APPDIR/usr/share/shoggoth/"
mkdir -p "$APPDIR/usr/share/shoggoth/fonts" && cp docs/plans/assets/fonts/OFL-*.txt "$APPDIR/usr/share/shoggoth/fonts/"   # the fonts embedded in index.html
# the sandboxed catalog test runs as another user: everything we ship must be world-readable
chmod -R a+rX "$APPDIR/usr/share/shoggoth"

# Node (just the binary) and Pi (its self-contained bundle, plus jiti, which it loads extensions with).
tar -xJf "$CACHE/node.tar.xz" -C "$APPDIR/usr/node" --strip-components=1 "node-v${NODE_VER}-linux-x64/bin/node" "node-v${NODE_VER}-linux-x64/LICENSE"
tar -xzf "$CACHE/pi.tgz" -C "$APPDIR/usr/pi" --strip-components=1 package/package.json package/dist/bundle
tar -xzf "$CACHE/jiti.tgz" -C "$APPDIR/usr/pi/node_modules/jiti" --strip-components=1

# llama.cpp: the processor-only build, where llamacpp.bundled_root() looks for it (usr/llamacpp/<tag>/cpu).
tar -xzf "$CACHE/llama-cpu.tar.gz" -C "$APPDIR/usr/llamacpp/$LLAMA_TAG/cpu" --strip-components=1
[ -x "$APPDIR/usr/llamacpp/$LLAMA_TAG/cpu/llama-server" ] || { echo "llama.cpp is missing from the image" >&2; exit 1; }
cp packaging/AppRun packaging/shoggoth.desktop packaging/shoggoth.png "$APPDIR/"
cp packaging/shoggoth.png "$APPDIR/.DirIcon"
# Desktop entry and AppStream metadata where desktop catalogs look for them (the copy at the root is what appimagetool needs).
mkdir -p "$APPDIR/usr/share/applications" "$APPDIR/usr/share/metainfo"
cp packaging/shoggoth.desktop "$APPDIR/usr/share/applications/"
cp packaging/io.github.yudhanjaya.Shoggoth.appdata.xml "$APPDIR/usr/share/metainfo/"

# Smoke test what is bundled before packaging.
"$APPDIR/usr/llamacpp/$LLAMA_TAG/cpu/llama-server" --version
"$APPDIR/usr/python/bin/python3" -I "$APPDIR/usr/share/shoggoth/server.py" --version
"$APPDIR/usr/node/bin/node" "$APPDIR/usr/pi/dist/bundle/cli.js" --version

FILE="$OUT/Shoggoth-$VERSION-x86_64.AppImage"
ARCH=x86_64 "$CACHE/appimagetool" --appimage-extract-and-run --comp zstd \
  -u "gh-releases-zsync|yudhanjaya|Shoggoth|latest|Shoggoth-*x86_64.AppImage.zsync" "$APPDIR" "$FILE"
chmod +x "$FILE"
( cd "$OUT" && sha256sum "$(basename "$FILE")" > "$(basename "$FILE").sha256" )
echo "Built $FILE"
