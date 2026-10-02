#!/bin/bash
set -euo pipefail
install_root="${ALICIA_OWNER_MIC_HOME:-$HOME/.alicia-owner-mic}"
export PYTHONPATH="$install_root/app"
exec "$install_root/runtime/bin/python" -m alicia.owner_microphone "$@"
