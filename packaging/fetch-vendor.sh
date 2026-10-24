#!/usr/bin/env bash
# Fetches Mermaid (MIT), which draws the diagrams that appear in Chat, into zest/vendor/. Pinned by npm's sha512.
# The AppImage build runs this; from a source checkout, run it once if you want diagrams to render.
set -euo pipefail
cd "$(dirname "$0")/.."
VER="12.2.0"
URL="https://registry.npmjs.org/mermaid/-/mermaid-${VER}.tgz"
INT="sha512-w7nr/SFrLO7BaVTs3dQTvYSV6nYSBj0Yy9DjBhsIJtAcHqRzXSq4Pg32ujHZ/AdWsAMNEvvhbm5dt9E2tOiHzw=="
OUT=zest/vendor
[ -f "$OUT/mermaid.min.js" ] && [ -f "$OUT/mermaid.version" ] && [ "$(cat "$OUT/mermaid.version")" = "$VER" ] && exit 0
mkdir -p "$OUT" build/cache
TGZ="build/cache/mermaid-${VER}.tgz"
if ! [ -f "$TGZ" ] || [ "sha512-$(openssl dgst -sha512 -binary "$TGZ" | base64 -w0)" != "$INT" ]; then
  echo "Downloading mermaid ${VER} (about 26 MB)"
  curl -fsSL "$URL" -o "$TGZ"
  [ "sha512-$(openssl dgst -sha512 -binary "$TGZ" | base64 -w0)" = "$INT" ] || { echo "Integrity mismatch for mermaid" >&2; exit 1; }
fi
tar -xzf "$TGZ" -C "$OUT" --strip-components=2 package/dist/mermaid.min.js
tar -xzf "$TGZ" -O package/LICENSE > "$OUT/mermaid.LICENSE"
echo "$VER" > "$OUT/mermaid.version"
