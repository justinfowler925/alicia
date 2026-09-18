#!/bin/zsh
# Persistent SSH tunnel: laptop localhost:8767 -> Studio Atlas6 :8767
# Mirrors atlas-chat-tunnel.sh (Atlas5 on :8766).
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ATLAS_HOST="${ATLAS_SSH_HOST:-jfstudio@justins-mac-studio-1.tailbaa084.ts.net}"
LOCAL_PORT="${BRUTUS_LOCAL_PORT:-8767}"
REMOTE_PORT="${BRUTUS_REMOTE_PORT:-8767}"
HEALTH_URL="${BRUTUS_HEALTH_URL:-http://127.0.0.1:${LOCAL_PORT}/api/healthz}"
CHECK_EVERY="${BRUTUS_TUNNEL_CHECK_S:-20}"

log() { print -u2 -- "[brutus-tunnel $(date '+%Y-%m-%dT%H:%M:%S')] $*"; }

cleanup() {
  if [[ -n "${SSH_PID:-}" ]] && kill -0 "$SSH_PID" 2>/dev/null; then
    kill "$SSH_PID" 2>/dev/null || true
    wait "$SSH_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

while true; do
  for pid in $(lsof -tiTCP:"$LOCAL_PORT" -sTCP:LISTEN 2>/dev/null || true); do
    log "killing stale listener pid=$pid on :$LOCAL_PORT"
    kill "$pid" 2>/dev/null || true
  done
  sleep 1

  log "starting ssh -L ${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT} -> $ATLAS_HOST"
  # Own connection — do NOT attach to ControlMaster mux (mux -N exits immediately
  # after registering the forward on the master, which breaks KeepAlive tunnels).
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
