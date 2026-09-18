#!/bin/zsh
# Persistent SSH tunnel: laptop localhost:7900 -> Studio Rapid-MLX router :7900
# OpenAI-compatible path for Cursor / Qwen Code / OpenCode:
#   http://127.0.0.1:7900/v1   model: qwen3.8-27b
# Mirrors atlas-chat-tunnel.sh. Live node is justins-mac-studio-1.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 100.93.125.5 (justins-mac-studio) is a stale Tailscale peer.
ATLAS_HOST="${ATLAS_SSH_HOST:-jfstudio@100.102.92.119}"
LOCAL_PORT="${STUDIO_MODEL_LOCAL_PORT:-7900}"
REMOTE_PORT="${STUDIO_MODEL_REMOTE_PORT:-7900}"
HEALTH_URL="${STUDIO_MODEL_HEALTH_URL:-http://127.0.0.1:${LOCAL_PORT}/v1/models}"
CHECK_EVERY="${STUDIO_MODEL_TUNNEL_CHECK_S:-20}"

log() { print -u2 -- "[studio-model-tunnel $(date '+%Y-%m-%dT%H:%M:%S')] $*"; }

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

  sleep 2
  FAIL=0
  while kill -0 "$SSH_PID" 2>/dev/null; do
    if curl -fsS -m 3 "$HEALTH_URL" >/dev/null 2>&1; then
      FAIL=0
    else
      FAIL=$((FAIL + 1))
      log "models miss count=$FAIL"
      if [[ "$FAIL" -ge 3 ]]; then
        log "/v1/models failed 3x — restarting tunnel"
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
