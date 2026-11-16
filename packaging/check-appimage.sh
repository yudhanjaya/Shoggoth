#!/usr/bin/env bash
# Starts a built AppImage on a throwaway profile and checks it the way a person would first meet it:
#   it starts, says the version it should, serves the page, lists the recommended models and runs its bundled llama.cpp;
#   then it is told to stop the way it stops itself (SIGTERM to Shoggoth), and nothing it started may still be running.
# Usage: packaging/check-appimage.sh dist/Shoggoth-<version>-x86_64.AppImage [expected-version]
# Never touches your own profile: everything lives in a temporary folder that is removed afterwards.
set -euo pipefail
APP="$(realpath "$1")"
WANT="${2:-$(basename "$APP" | sed -n 's/^Shoggoth-\(.*\)-x86_64\.AppImage$/\1/p')}"
TMP="$(mktemp -d)"
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
LOG="$TMP/run.log"
fail() { echo "FAIL: $*" >&2; echo "--- log" >&2; tail -40 "$LOG" >&2 || true; cleanup; exit 1; }
cleanup() {
  pkill -KILL -f "$TMP/" 2>/dev/null || true          # anything still holding the throwaway profile
  [ -n "${APID:-}" ] && kill -KILL "$APID" 2>/dev/null || true
  rm -rf "$TMP"
}

# A CI machine has no FUSE: let the AppImage unpack itself instead of mounting.
export APPIMAGE_EXTRACT_AND_RUN=1
export SHOGGOTH_AUTH=off XDG_DATA_HOME="$TMP/data" XDG_CONFIG_HOME="$TMP/config" HOME="$TMP/home" CUDA_VISIBLE_DEVICES=""
mkdir -p "$HOME"
cd "$TMP"
"$APP" --no-browser --port "$PORT" >"$LOG" 2>&1 &
APID=$!

for _ in $(seq 1 90); do
  curl -fs "http://127.0.0.1:$PORT/ui/info" >/dev/null 2>&1 && break
  kill -0 "$APID" 2>/dev/null || fail "the AppImage exited while starting"
  sleep 1
done
curl -fs "http://127.0.0.1:$PORT/ui/info" >/dev/null || fail "no answer on port $PORT after 90 s"

ver="$(curl -fs -X POST "http://127.0.0.1:$PORT/ui/ping" | python3 -c 'import json,sys; print(json.load(sys.stdin)["zest"])')"
[ "$ver" = "$WANT" ] || fail "it says it is $ver, the file says $WANT"
echo "ok: version $ver"

code="$(curl -s -o "$TMP/page.html" -w '%{http_code}' "http://127.0.0.1:$PORT/")"
[ "$code" = 200 ] && grep -q 'Heavy on the cosmos' "$TMP/page.html" || fail "the page didn't load ($code)"
echo "ok: the page loads"

n="$(curl -fs "http://127.0.0.1:$PORT/ui/models/list" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(len([m for m in d if m.get("suggested")]))')"
[ "$n" -gt 0 ] || fail "no recommended models in /ui/models/list"
echo "ok: $n recommended models listed"

tag="$(curl -fs "http://127.0.0.1:$PORT/ui/info" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("llama_tag") or "")')"
[ -n "$tag" ] || fail "no bundled llama.cpp build found"
for _ in $(seq 1 60); do pgrep -f "llama-server.*$TMP/" >/dev/null && break; sleep 1; done
pgrep -f "llama-server.*$TMP/" >/dev/null || fail "the bundled llama.cpp ($tag) didn't start"
echo "ok: llama.cpp $tag running"

# Stop it the way Shoggoth stops itself when its last tab closes: SIGTERM to the server.
SPID="$(pgrep -f "server.py.*--port $PORT" | head -1)"
[ -n "$SPID" ] || fail "can't find Shoggoth's server process"
kill -TERM "$SPID"
for _ in $(seq 1 40); do
  pgrep -f "$TMP/" >/dev/null || pgrep -f "server.py.*--port $PORT" >/dev/null || break
  sleep 1
done
left="$(pgrep -fa "$TMP/|server.py.*--port $PORT" || true)"
[ -z "$left" ] || fail "still running 40 s after Shoggoth was told to stop: $left"
echo "ok: stopping Shoggoth stopped llama.cpp too"

cleanup
echo "AppImage check passed: $(basename "$APP")"
