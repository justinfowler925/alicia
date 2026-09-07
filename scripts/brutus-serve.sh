#!/bin/zsh
# Launchd entrypoint for Brutus UI (:8768).
# Credentials are delivered by one fail-closed 1Password profile.
set -euo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

CREDENTIAL_RUN="${CREDENTIAL_RUN:-$HOME/fowler-brain/scripts/credential-run}"

# Where the CODE lives. Defaults to the dedicated service worktree so the daemon
# is never at the mercy of whichever branch a shared checkout happens to be on —
# that bit us twice in one day. Override for local runs.
BRUTUS_APP_DIR="${BRUTUS_APP_DIR:-$HOME/.brutus/app}"
[ -d "$BRUTUS_APP_DIR" ] || BRUTUS_APP_DIR="/Users/justinfowler/Projects/brutus"
cd "$BRUTUS_APP_DIR"

# Where the STATE lives — outside every checkout, so a redeploy, a branch
# switch or a fresh clone cannot empty Brutus's memory.
export BRUTUS_STATE_DIR="${BRUTUS_STATE_DIR:-$HOME/.brutus/state}"
export BRUTUS_CONFIG="${BRUTUS_CONFIG:-$BRUTUS_APP_DIR/config.yaml}"
export BRUTUS_DEPLOY_MANIFEST="${BRUTUS_DEPLOY_MANIFEST:-$BRUTUS_APP_DIR/.brutus-deploy.json}"
RUNTIME_VENV="${BRUTUS_RUNTIME_VENV:-$BRUTUS_APP_DIR/.runtime-venv}"
[ -x "$RUNTIME_VENV/bin/brutus" ] || RUNTIME_VENV="$BRUTUS_APP_DIR/.venv"

# This venv belongs to THIS directory and is editable-installed against it.
# Sharing the checkout's venv silently imported brutus from the checkout — its
# .pth import hook wins over cwd and over PYTHONPATH — so the daemon ran code
# from a directory nobody had deployed to.
# The loopback LiveKit jobs create this once (0600). Loading the same file is
# what makes the token Brutus mints match the local media server.
[[ -s "$HOME/.brutus/livekit.env" ]] && source "$HOME/.brutus/livekit.env"

exec "$BRUTUS_APP_DIR/scripts/run-with-credential-backoff.sh" \
  brutus-core -- "$RUNTIME_VENV/bin/brutus" serve
