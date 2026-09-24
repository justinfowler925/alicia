#!/usr/bin/env bash
# Verify the session event stream against a REAL uvicorn process.
#
# There is no in-process test for this. An infinite SSE stream driven through
# TestClient or httpx.ASGITransport hangs the suite instead of failing it —
# neither transport can abandon a generator that legitimately never ends.
# Chasing that produced a harness artifact, not a bug, so the stream is proven
# where it actually runs.
#
# What this asserts, in order:
#   1. the stream opens immediately, before anything has happened
#   2. a turn posted afterwards arrives on the open stream
#   3. the frames carry both renderings — full text and the spoken version
#
#   ./scripts/verify-sse.sh          # exit 0 = the stream works
set -uo pipefail

cd "$(dirname "$0")/.."
PY="${PY:-$HOME/Projects/alicia/.venv/bin/python}"
PORT="${PORT:-8799}"          # deliberately not 8768 — never disturb the live daemon
BASE="http://127.0.0.1:$PORT"
TMP="$(mktemp -d)"
trap 'kill "${SRV:-0}" 2>/dev/null; rm -rf "$TMP"' EXIT

echo "==> starting a server on :$PORT (state in $TMP)"
ALICIA_STATE_DIR="$TMP" "$PY" - "$PORT" <<'PYEOF' >"$TMP/server.log" 2>&1 &
import sys, uvicorn
from alicia.config import load_config
from alicia.server import create_app
cfg = load_config()
app = create_app(cfg, start_watchdog=False)
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
PYEOF
SRV=$!

for _ in $(seq 1 40); do
  curl -sf -m 1 "$BASE/api/session/list" >/dev/null 2>&1 && break
  sleep 0.25
done
curl -sf -m 2 "$BASE/api/session/list" >/dev/null || { echo "FAIL: server never came up"; cat "$TMP/server.log"; exit 1; }

SID=$(curl -s -m 5 -X POST -H 'content-type: application/json' \
        -d '{"title":"sse check"}' "$BASE/api/session/open" \
      | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
echo "==> session $SID"

# Open the stream BEFORE saying anything, so "arrives live" means what it says.
# Generous: a real (unstubbed) LLM turn can take 15s+, and a stream
# that expires mid-turn looks exactly like a dropped event.
curl -sN -m 120 "$BASE/api/session/$SID/events" >"$TMP/stream.txt" &
STREAM=$!
sleep 1

grep -q '"kind": "open"' "$TMP/stream.txt" \
  && echo "ok   stream opened before any activity" \
  || { echo "FAIL: no open frame"; cat "$TMP/stream.txt"; exit 1; }

echo "==> posting a turn"
curl -s -m 60 -X POST -H 'content-type: application/json' \
  -d '{"message":"what needs me","channel":"voice"}' \
  "$BASE/api/session/$SID/say" >"$TMP/say.json"
sleep 1
kill "$STREAM" 2>/dev/null; wait "$STREAM" 2>/dev/null

FAILED=0
check () { # $1=pattern $2=label $3=file
  if grep -q "$1" "$3"; then echo "ok   $2"; else echo "FAIL $2"; FAILED=1; fi
}
check '"kind": "turn"'  "the user turn arrived live on the open stream" "$TMP/stream.txt"
check '"kind": "reply"' "the reply arrived live on the open stream"     "$TMP/stream.txt"
check '"channel": "voice"' "the transport is recorded on the turn"      "$TMP/stream.txt"
check '"spoken"'        "frames carry the spoken rendering"             "$TMP/stream.txt"
check '"reply"'         "the say response carries the screen rendering" "$TMP/say.json"

echo
echo "--- frames observed ---"
grep -o '"kind": "[a-z]*"' "$TMP/stream.txt" | sort | uniq -c

[ "$FAILED" -eq 0 ] && echo -e "\nSSE stream verified against a live server." || echo -e "\nSSE verification FAILED."
exit "$FAILED"
