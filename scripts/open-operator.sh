#!/bin/zsh
# Open the standalone laptop Alicia UI (:8768) with a one-time owner pair ticket.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT="${ALICIA_ROOT:-$HOME/Projects/alicia}"
ALICIA_PORT="${ALICIA_SERVE_PORT:-8768}"
BASE="http://127.0.0.1:${ALICIA_PORT}"
LABEL="com.clearspeed.alicia"
# Ensure Alicia laptop serve is up
if ! lsof -iTCP:"$ALICIA_PORT" -sTCP:LISTEN -n -P 2>/dev/null | grep -q ":${ALICIA_PORT} "; then
  launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  for _ in {1..20}; do
    lsof -iTCP:"$ALICIA_PORT" -sTCP:LISTEN -n -P 2>/dev/null | grep -q ":${ALICIA_PORT} " && break
    sleep 0.5
  done
fi

if ! curl -s -o /dev/null --connect-timeout 3 "$BASE/"; then
  echo "Alicia not up on ${BASE}/. Try: cd ${ROOT} && source .venv/bin/activate && alicia serve" >&2
  echo "Logs: ~/.cursor/logs/alicia-serve.err.log" >&2
  exit 1
fi

TOKEN=""
if [[ -x "$ROOT/.venv/bin/alicia" ]]; then
  TOKEN="$("$ROOT/.venv/bin/alicia" owner-token 2>/dev/null || true)"
elif [[ -x "$HOME/.alicia/app/.venv/bin/alicia" ]]; then
  TOKEN="$("$HOME/.alicia/app/.venv/bin/alicia" owner-token 2>/dev/null || true)"
fi

URL="$BASE/"
if [[ -n "$TOKEN" ]]; then
  TICKET="$(curl -sS -X POST "$BASE/api/auth/pair" \
    -H "X-Alicia-Owner-Token: $TOKEN" \
    -H "content-type: application/json" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("ticket",""))' 2>/dev/null || true)"
  if [[ -n "$TICKET" ]]; then
    URL="$BASE/?pair=$TICKET"
  fi
fi

echo "Alicia (laptop): $URL"
open "$URL" 2>/dev/null || true
