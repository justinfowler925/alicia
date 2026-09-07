#!/bin/zsh
set -euo pipefail
BRUTUS_APP_DIR="${BRUTUS_APP_DIR:-$HOME/.brutus/app}"
source "$BRUTUS_APP_DIR/scripts/brutus-livekit-env.sh"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export BRUTUS_STATE_DIR="${BRUTUS_STATE_DIR:-$HOME/.brutus/state}"
export BRUTUS_CONFIG="${BRUTUS_CONFIG:-$BRUTUS_APP_DIR/config.yaml}"
RUNTIME_VENV="${BRUTUS_RUNTIME_VENV:-$BRUTUS_APP_DIR/.runtime-venv}"
[ -x "$RUNTIME_VENV/bin/python" ] || RUNTIME_VENV="$BRUTUS_APP_DIR/.venv"
CREDENTIAL_RUN="${CREDENTIAL_RUN:-$HOME/fowler-brain/scripts/credential-run}"
cd "$BRUTUS_STATE_DIR"
exec "$BRUTUS_APP_DIR/scripts/run-with-credential-backoff.sh" \
  brutus-core -- "$RUNTIME_VENV/bin/python" -m brutus.livekit_agent start
