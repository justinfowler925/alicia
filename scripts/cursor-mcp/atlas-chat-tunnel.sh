#!/bin/zsh
# Persistent SSH tunnel: laptop localhost:8766 -> Studio Atlas :8766
# Restarts when the forward dies OR when healthz stops responding (stale mux).
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ATLAS_HOST="${ATLAS_SSH_HOST:-jfstudio@justins-mac-studio-1.tailbaa084.ts.net}"
LOCAL_PORT="${ATLAS_LOCAL_PORT:-8766}"
REMOTE_PORT="${ATLAS_REMOTE_PORT:-8766}"
HEALTH_URL="${ATLAS_HEALTH_URL:-http://127.0.0.1:${LOCAL_PORT}/api/healthz}"
CHECK_EVERY="${ATLAS_TUNNEL_CHECK_S:-20}"

log() { print -u2 -- "[atlas-tunnel $(date '+%Y-%m-%dT%H:%M:%S')] $*"; }

cleanup() {
  if [[ -n "${SSH_PID:-}" ]] && kill -0 "$SSH_PID" 2>/dev/null; then
    kill "$SSH_PID" 2>/dev/null || true
    wait "$SSH_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

while true; do
  # Clear any orphan listener on the port before binding.
  for pid in $(lsof -tiTCP:"$LOCAL_PORT" -sTCP:LISTEN 2>/dev/null || true); do
    log "killing stale listener pid=$pid on :$LOCAL_PORT"
    kill "$pid" 2>/dev/null || true
  done
  sleep 1

  log "starting ssh -L ${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT} -> $ATLAS_HOST"
  # Own connection — mux'd -N exits right after registering the forward on master.
  ssh -N \
    -o ControlMaster=no \
    -o ControlPath=none \
    -o ExitOnForwardFailure=yes \
    -o ConnectTimeout=20 \
    -o ServerAliveInterval=20 \
    -o ServerAliveCountMax=3 \
    -o TCPKeepAlive=yes \
    -o BatchMode=yes \
    -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" \
    "$ATLAS_HOST" &
  SSH_PID=$!

  # Wait briefly for bind, then health-loop until ssh dies or health fails.
  sleep 2
  FAIL=0
  while kill -0 "$SSH_PID" 2>/dev/null; do
    if curl -fsS -m 3 "$HEALTH_URL" >/dev/null 2>&1; then
      FAIL=0
    else
      FAIL=$((FAIL + 1))
      log "healthz miss count=$FAIL"
      if [[ "$FAIL" -ge 3 ]]; then
        log "healthz failed 3x — restarting tunnel"
        kill "$SSH_PID" 2>/dev/null || true
        wait "$SSH_PID" 2>/dev/null || true
        SSH_PID=
        break
      fi
    fi
    sleep "$CHECK_EVERY"
  done

  if [[ -n "${SSH_PID:-}" ]]; then
    wait "$SSH_PID" 2>/dev/null || true
    log "ssh exited; retrying in 5s"
    SSH_PID=
  fi
  sleep 5
done
