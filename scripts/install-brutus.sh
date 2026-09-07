#!/usr/bin/env bash
# Install Brutus on the MacBook (laptop talking head).
set -euo pipefail

ROOT="${BRUTUS_ROOT:-$HOME/Projects/brutus}"
MCP_JSON="${HOME}/.cursor/mcp.json"

echo "==> Brutus install @ ${ROOT}"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -e ".[dev]" -q
pytest -q
python -c 'from brutus.security import configured_adapter_token; configured_adapter_token()'

# Ensure config exists
if [[ ! -f config.yaml ]]; then
  cp config.example.yaml config.yaml
  echo "wrote config.yaml — standalone mode enabled"
fi

# Merge MCP server entry if missing
python3 - <<'PY'
import json
from pathlib import Path
p = Path.home() / ".cursor" / "mcp.json"
data = {"mcpServers": {}}
if p.exists():
    data = json.loads(p.read_text())
servers = data.setdefault("mcpServers", {})
if "brutus" not in servers:
    root = Path.home() / "Projects" / "brutus"
    servers["brutus"] = {
        "command": "/bin/zsh",
        "args": ["-lc", f"cd {root} && source .venv/bin/activate && exec brutus-mcp"],
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    print("added brutus to ~/.cursor/mcp.json — reload MCP in Cursor")
else:
    print("brutus already in ~/.cursor/mcp.json")
PY

# Atlas is intentionally ignored. Stop an older Brutus-owned tunnel if present.
mkdir -p "${HOME}/.cursor/logs" "${HOME}/Library/LaunchAgents"
TUNNEL_DST="${HOME}/Library/LaunchAgents/com.clearspeed.brutus-tunnel.plist"
chmod +x "${ROOT}/scripts/brutus-tunnel.sh" "${ROOT}/scripts/open-operator.sh"
launchctl disable "gui/$(id -u)/com.clearspeed.brutus-tunnel" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.clearspeed.brutus-tunnel" 2>/dev/null || true
echo "Atlas tunnel disabled (standalone mode)"

# Laptop Brutus UI/API (:8768) — Cursor is the only reasoning backend.
SERVE_SRC="${ROOT}/launchd/com.clearspeed.brutus.plist"
SERVE_DST="${HOME}/Library/LaunchAgents/com.clearspeed.brutus.plist"
if [[ -f "$SERVE_SRC" ]]; then
  cp "$SERVE_SRC" "$SERVE_DST"
  launchctl bootout "gui/$(id -u)/com.clearspeed.brutus" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$SERVE_DST" 2>/dev/null || launchctl load "$SERVE_DST" 2>/dev/null || true
  echo "loaded com.clearspeed.brutus (localhost:8768 laptop face)"
fi

# Zoom → Brutus Notes feeder (Justin action items → Inbox for Promote)
ZOOM_SRC="${ROOT}/launchd/com.clearspeed.brutus-zoom-notes.plist"
ZOOM_DST="${HOME}/Library/LaunchAgents/com.clearspeed.brutus-zoom-notes.plist"
chmod +x "${ROOT}/scripts/feed_zoom_to_brutus_notes.py" \
         "${ROOT}/scripts/schedule-zoom-brutus-notes.sh"
if [[ -f "$ZOOM_SRC" ]]; then
  cp "$ZOOM_SRC" "$ZOOM_DST"
  launchctl bootout "gui/$(id -u)/com.clearspeed.brutus-zoom-notes" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$ZOOM_DST" 2>/dev/null || launchctl load "$ZOOM_DST" 2>/dev/null || true
  echo "loaded com.clearspeed.brutus-zoom-notes (Zoom actions → Notes Inbox every 5m)"
fi

echo "==> OK. Try: brutus health"
echo "    Brutus UI:    bash scripts/open-operator.sh  → http://127.0.0.1:8768/"
echo "    Reload Cursor MCP panels after install."
