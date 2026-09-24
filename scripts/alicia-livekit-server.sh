#!/bin/zsh
set -euo pipefail
# Brutus -> Alicia rename: until cutover, fall back to ~/.brutus and BRUTUS_* env.
ALICIA_HOME="${ALICIA_HOME:-$HOME/.alicia}"
[ -d "$ALICIA_HOME" ] || [ ! -d "$HOME/.brutus" ] || ALICIA_HOME="$HOME/.brutus"
for _legacy in $(env | sed -n 's/^BRUTUS_\([A-Za-z0-9_]*\)=.*/\1/p'); do
  eval "[ -n \"\${ALICIA_${_legacy}+x}\" ] || export ALICIA_${_legacy}=\"\${BRUTUS_${_legacy}}\""
done
unset _legacy
source "${ALICIA_APP_DIR:-$ALICIA_HOME/app}/scripts/alicia-livekit-env.sh"

# Never put the key pair in argv: process arguments are visible to every local
# process inspector. LiveKit reads the same credentials from a mode-0600 file.
LIVEKIT_KEY_FILE="${ALICIA_LIVEKIT_KEY_FILE:-$ALICIA_HOME/livekit.keys}"
mkdir -p "${LIVEKIT_KEY_FILE:h}"
umask 077
_key_tmp="${LIVEKIT_KEY_FILE}.tmp.$$"
trap '/bin/rm -f "$_key_tmp"' EXIT
print -r -- "${LIVEKIT_API_KEY}: ${LIVEKIT_API_SECRET}" > "$_key_tmp"
/bin/chmod 600 "$_key_tmp"
/bin/mv -f "$_key_tmp" "$LIVEKIT_KEY_FILE"
trap - EXIT
unset LIVEKIT_API_KEY LIVEKIT_API_SECRET

exec /opt/homebrew/bin/livekit-server \
  --bind 127.0.0.1 \
  --node-ip 127.0.0.1 \
  --udp-port 7882 \
  --rtc.tcp_port 0 \
  --key-file "$LIVEKIT_KEY_FILE"
