#!/bin/zsh
# Create/load loopback-only LiveKit credentials shared by server, worker, and Alicia.
set -euo pipefail
# Brutus -> Alicia rename: until cutover, fall back to ~/.brutus and BRUTUS_* env.
ALICIA_HOME="${ALICIA_HOME:-$HOME/.alicia}"
[ -d "$ALICIA_HOME" ] || [ ! -d "$HOME/.brutus" ] || ALICIA_HOME="$HOME/.brutus"
for _legacy in $(env | sed -n 's/^BRUTUS_\([A-Za-z0-9_]*\)=.*/\1/p'); do
  eval "[ -n \"\${ALICIA_${_legacy}+x}\" ] || export ALICIA_${_legacy}=\"\${BRUTUS_${_legacy}}\""
done
unset _legacy
LIVEKIT_ENV_FILE="${ALICIA_LIVEKIT_ENV_FILE:-$ALICIA_HOME/livekit.env}"
mkdir -p "${LIVEKIT_ENV_FILE:h}"
if [[ ! -s "$LIVEKIT_ENV_FILE" ]]; then
  umask 077
  _secret=$(/usr/bin/openssl rand -hex 32)
  _tmp="${LIVEKIT_ENV_FILE}.tmp.$$"
  {
    print -r -- 'export LIVEKIT_URL=ws://127.0.0.1:7880'
    print -r -- 'export LIVEKIT_API_KEY=alicialocal'
    print -r -- "export LIVEKIT_API_SECRET=$_secret"
  } > "$_tmp"
  /bin/chmod 600 "$_tmp"
  /bin/mv -n "$_tmp" "$LIVEKIT_ENV_FILE" || /bin/rm -f "$_tmp"
  unset _secret _tmp
fi
source "$LIVEKIT_ENV_FILE"
