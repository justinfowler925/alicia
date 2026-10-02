#!/bin/bash
# Install an isolated local runtime; never change Voice Control or auto-start it.
set -euo pipefail
source_root=$(cd "$(dirname "$0")/.." && pwd -P)
install_root="${ALICIA_OWNER_MIC_HOME:-$HOME/.alicia-owner-mic}"
uv_bin="${UV_BIN:-/opt/homebrew/bin/uv}"
mkdir -p "$install_root/app/alicia"
"$uv_bin" venv --python 3.12 "$install_root/runtime"
"$uv_bin" pip install --python "$install_root/runtime/bin/python" -r "$source_root/scripts/owner-microphone-requirements.txt"
for file in __init__.py paths.py voice_identity.py owner_microphone.py; do
  cp "$source_root/alicia/$file" "$install_root/app/alicia/$file"
done
cp "$source_root/scripts/owner-microphone.sh" "$install_root/run.sh"
chmod 755 "$install_root/run.sh"
git -C "$source_root" rev-parse HEAD > "$install_root/source-sha"
echo "Installed. First run: $install_root/run.sh --observe --seconds 60"
