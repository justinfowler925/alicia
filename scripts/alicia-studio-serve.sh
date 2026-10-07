#!/bin/bash
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"
export ALICIA_HOME="${ALICIA_HOME:-$HOME/.alicia}"
export ALICIA_APP_DIR="${ALICIA_APP_DIR:-$ALICIA_HOME/app}"
export ALICIA_CONFIG="${ALICIA_CONFIG:-$ALICIA_HOME/config.yaml}"
export ALICIA_STATE_DIR="${ALICIA_STATE_DIR:-$ALICIA_HOME/state}"
export ALICIA_WATCH_ENABLED=1
export ALICIA_COWORKER_WATCH_ENABLED=1
export ALICIA_WATCH_SLACK_OWNER=U03TVK7B057
export ALICIA_CONVERSATION_PROVIDER=cursor
export ALICIA_VOICE_PROVIDER=openai_live
export ALICIA_VOICE_CURSOR_PROPOSALS=1
export ALICIA_CURSOR_MODEL=auto
export AGENT_CLI_CREDENTIAL_STORE=file
# Keep the real loopback proxy peer for the Tailscale identity boundary.
export FORWARDED_ALLOW_IPS=""
export ALICIA_STUDIO_SSH=jfstudio@100.102.92.119
export CREDENTIAL_CONTRACT="$ALICIA_APP_DIR/credentials/studio.json"
export ALICIA_PUBLIC_ORIGIN=https://justins-mac-studio-1.tailbaa084.ts.net:8768
export ALICIA_TAILSCALE_OWNER=justin@justinfowler.com
# Optional per-device allowlist (comma-separated hostnames/IPs and/or tags).
# Leave empty until Tailscale ACL tags devices as tag:owner (see security docs).
export ALICIA_TAILSCALE_ALLOWED_NODES="${ALICIA_TAILSCALE_ALLOWED_NODES:-}"
export ALICIA_TAILSCALE_ALLOWED_TAGS="${ALICIA_TAILSCALE_ALLOWED_TAGS:-}"
export ALICIA_SERVE_PROXY_PORT="${ALICIA_SERVE_PROXY_PORT:-8767}"
export ALICIA_SERVE_UPSTREAM="${ALICIA_SERVE_UPSTREAM:-http://127.0.0.1:8768}"
cd "$ALICIA_APP_DIR"

# State dir must not be group/world readable (sessions, DBs, tokens).
mkdir -p "$ALICIA_STATE_DIR"
chmod 700 "$ALICIA_STATE_DIR" 2>/dev/null || true
chmod 600 "$ALICIA_STATE_DIR"/*.sqlite "$ALICIA_STATE_DIR"/*.token "$ALICIA_STATE_DIR"/*.proof 2>/dev/null || true

# Mint/load Serve proof, then front Alicia with the proof-injecting proxy.
# Tailscale Serve targets the proxy port; forged headers on :8768 alone fail.
PROOF="$("$ALICIA_APP_DIR/.venv/bin/python" -c 'from alicia.security import configured_serve_proof; print(configured_serve_proof())')"
export ALICIA_SERVE_PROOF="$PROOF"
PROXY_PID_FILE="$ALICIA_STATE_DIR/serve-proxy.pid"
if [[ -f "$PROXY_PID_FILE" ]]; then
  old_pid=$(cat "$PROXY_PID_FILE" 2>/dev/null || true)
  if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
    sleep 0.2
  fi
fi
"$ALICIA_APP_DIR/.venv/bin/python" "$ALICIA_APP_DIR/scripts/alicia-serve-proxy.py" \
  --listen "$ALICIA_SERVE_PROXY_PORT" \
  --upstream "$ALICIA_SERVE_UPSTREAM" \
  >>"$ALICIA_HOME/logs/serve-proxy.log" 2>&1 &
echo $! >"$PROXY_PID_FILE"

if (( $# == 0 )); then set -- serve; fi
exec "$HOME/.config/fowler-credentials/scripts/credential-run" alicia-studio -- "$ALICIA_APP_DIR/.venv/bin/alicia" "$@"
