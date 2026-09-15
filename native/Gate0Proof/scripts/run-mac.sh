#!/usr/bin/env bash
# Build and launch the Gate 0 Mac Voice Control proof.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# CLT-only machines need the native build system; default xcode build-system
# also works once full Xcode is installed.
if [[ ! -d /Applications/Xcode.app ]]; then
  BUILD_SYS=(--build-system native)
else
  BUILD_SYS=()
fi
swift build --product Gate0Mac -c debug "${BUILD_SYS[@]}"
BIN="$(swift build --show-bin-path -c debug "${BUILD_SYS[@]}")/Gate0Mac"
mkdir -p "$HOME/.brutus/gate0-logs"
echo "Launching $BIN"
echo "Event logs → $HOME/.brutus/gate0-logs"
echo "Enable macOS Voice Control, focus “Brutus draft”, speak continuously."
exec "$BIN"
