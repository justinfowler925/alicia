#!/bin/zsh
# Launchd entrypoint for Alicia UI (:8768).
# Credentials are delivered by one fail-closed 1Password profile.
set -euo pipefail
# Brutus -> Alicia rename: until cutover, fall back to ~/.brutus and BRUTUS_* env.
ALICIA_HOME="${ALICIA_HOME:-$HOME/.alicia}"
[ -d "$ALICIA_HOME" ] || [ ! -d "$HOME/.brutus" ] || ALICIA_HOME="$HOME/.brutus"
for _legacy in $(env | sed -n 's/^BRUTUS_\([A-Za-z0-9_]*\)=.*/\1/p'); do
  eval "[ -n \"\${ALICIA_${_legacy}+x}\" ] || export ALICIA_${_legacy}=\"\${BRUTUS_${_legacy}}\""
done
unset _legacy

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

CREDENTIAL_RUN="${CREDENTIAL_RUN:-$HOME/fowler-brain/scripts/credential-run}"

# Where the CODE lives. Defaults to the dedicated service worktree so the daemon
# is never at the mercy of whichever branch a shared checkout happens to be on —
# that bit us twice in one day. Override for local runs.
ALICIA_APP_DIR="${ALICIA_APP_DIR:-$ALICIA_HOME/app}"
[ -d "$ALICIA_APP_DIR" ] || ALICIA_APP_DIR="/Users/justinfowler/Projects/alicia"
cd "$ALICIA_APP_DIR"

# Where the STATE lives — outside every checkout, so a redeploy, a branch
# switch or a fresh clone cannot empty Alicia's memory.
export ALICIA_STATE_DIR="${ALICIA_STATE_DIR:-$ALICIA_HOME/state}"
export ALICIA_CONFIG="${ALICIA_CONFIG:-$ALICIA_APP_DIR/config.yaml}"
export ALICIA_DEPLOY_MANIFEST="${ALICIA_DEPLOY_MANIFEST:-$ALICIA_APP_DIR/.alicia-deploy.json}"
RUNTIME_VENV="${ALICIA_RUNTIME_VENV:-$ALICIA_APP_DIR/.runtime-venv}"
[ -x "$RUNTIME_VENV/bin/alicia" ] || RUNTIME_VENV="$ALICIA_APP_DIR/.venv"

# This venv belongs to THIS directory and is editable-installed against it.
# Sharing the checkout's venv silently imported alicia from the checkout — its
# .pth import hook wins over cwd and over PYTHONPATH — so the daemon ran code
# from a directory nobody had deployed to.
# The loopback LiveKit jobs create this once (0600). Loading the same file is
# what makes the token Alicia mints match the local media server.
[[ -s "$ALICIA_HOME/livekit.env" ]] && source "$ALICIA_HOME/livekit.env"

exec "$ALICIA_APP_DIR/scripts/run-with-credential-backoff.sh" \
  brutus-core -- "$RUNTIME_VENV/bin/alicia" serve
