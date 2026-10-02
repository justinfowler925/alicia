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
cd "$ALICIA_APP_DIR"
if (( $# == 0 )); then set -- serve; fi
exec "$HOME/.config/fowler-credentials/scripts/credential-run" alicia-studio -- "$ALICIA_APP_DIR/.venv/bin/alicia" "$@"
