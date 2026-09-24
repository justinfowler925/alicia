#!/bin/zsh
set -euo pipefail
# Brutus -> Alicia rename: until cutover, fall back to ~/.brutus and BRUTUS_* env.
ALICIA_HOME="${ALICIA_HOME:-$HOME/.alicia}"
[ -d "$ALICIA_HOME" ] || [ ! -d "$HOME/.brutus" ] || ALICIA_HOME="$HOME/.brutus"
for _legacy in $(env | sed -n 's/^BRUTUS_\([A-Za-z0-9_]*\)=.*/\1/p'); do
  eval "[ -n \"\${ALICIA_${_legacy}+x}\" ] || export ALICIA_${_legacy}=\"\${BRUTUS_${_legacy}}\""
done
unset _legacy
ALICIA_APP_DIR="${ALICIA_APP_DIR:-$ALICIA_HOME/app}"
source "$ALICIA_APP_DIR/scripts/alicia-livekit-env.sh"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export ALICIA_STATE_DIR="${ALICIA_STATE_DIR:-$ALICIA_HOME/state}"
export ALICIA_CONFIG="${ALICIA_CONFIG:-$ALICIA_APP_DIR/config.yaml}"
RUNTIME_VENV="${ALICIA_RUNTIME_VENV:-$ALICIA_APP_DIR/.runtime-venv}"
[ -x "$RUNTIME_VENV/bin/python" ] || RUNTIME_VENV="$ALICIA_APP_DIR/.venv"
CREDENTIAL_RUN="${CREDENTIAL_RUN:-$HOME/fowler-brain/scripts/credential-run}"
cd "$ALICIA_STATE_DIR"
exec "$ALICIA_APP_DIR/scripts/run-with-credential-backoff.sh" \
  brutus-core -- "$RUNTIME_VENV/bin/python" -m alicia.livekit_agent start
