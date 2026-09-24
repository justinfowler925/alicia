#!/bin/zsh
# Open the standalone laptop Alicia UI (:8768).
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT="${ALICIA_ROOT:-$HOME/Projects/alicia}"
ALICIA_PORT="${ALICIA_SERVE_PORT:-8768}"
URL="http://127.0.0.1:${ALICIA_PORT}/"
LABEL="com.clearspeed.alicia"
# Ensure Alicia laptop serve is up
if ! lsof -iTCP:"$ALICIA_PORT" -sTCP:LISTEN -n -P 2>/dev/null | grep -q ":${ALICIA_PORT} "; then
  launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  for _ in {1..20}; do
    lsof -iTCP:"$ALICIA_PORT" -sTCP:LISTEN -n -P 2>/dev/null | grep -q ":${ALICIA_PORT} " && break
    sleep 0.5
  done
fi

if ! curl -s -o /dev/null --connect-timeout 3 "$URL"; then
  echo "Alicia not up on ${URL}. Try: cd ${ROOT} && source .venv/bin/activate && alicia serve" >&2
  echo "Logs: ~/.cursor/logs/alicia-serve.err.log" >&2
  exit 1
fi

echo "Alicia (laptop): $URL"
open "$URL" 2>/dev/null || true
