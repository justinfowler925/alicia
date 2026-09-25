#!/usr/bin/env bash
# Install Alicia on the MacBook (laptop talking head).
set -euo pipefail

ROOT="${ALICIA_ROOT:-$HOME/Projects/alicia}"
MCP_JSON="${HOME}/.cursor/mcp.json"

echo "==> Alicia install @ ${ROOT}"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -e ".[dev]" -q
pytest -q
python -c 'from alicia.security import configured_adapter_token; configured_adapter_token()'

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
if "alicia" not in servers:
    root = Path.home() / "Projects" / "alicia"
    servers["alicia"] = {
        "command": "/bin/zsh",
        "args": ["-lc", f"cd {root} && source .venv/bin/activate && exec alicia-mcp"],
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    print("added alicia to ~/.cursor/mcp.json — reload MCP in Cursor")
else:
    print("alicia already in ~/.cursor/mcp.json")
PY

# Atlas is intentionally ignored. Stop an older Alicia-owned tunnel if present.
mkdir -p "${HOME}/.cursor/logs" "${HOME}/Library/LaunchAgents"
TUNNEL_DST="${HOME}/Library/LaunchAgents/com.clearspeed.alicia-tunnel.plist"
chmod +x "${ROOT}/scripts/alicia-tunnel.sh" "${ROOT}/scripts/open-operator.sh"
launchctl disable "gui/$(id -u)/com.clearspeed.alicia-tunnel" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.clearspeed.alicia-tunnel" 2>/dev/null || true
echo "Atlas tunnel disabled (standalone mode)"

# Laptop Alicia UI/API (:8768) — Cursor is the only reasoning backend.
SERVE_SRC="${ROOT}/launchd/com.clearspeed.alicia.plist"
SERVE_DST="${HOME}/Library/LaunchAgents/com.clearspeed.alicia.plist"
if [[ -f "$SERVE_SRC" ]]; then
  cp "$SERVE_SRC" "$SERVE_DST"
  launchctl bootout "gui/$(id -u)/com.clearspeed.alicia" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$SERVE_DST" 2>/dev/null || launchctl load "$SERVE_DST" 2>/dev/null || true
  echo "loaded com.clearspeed.alicia (localhost:8768 laptop face)"
fi

# Zoom, Salesforce meeting notes and GitHub facts are ingested by Scout on
# Studio; Alicia imports them when it runs (alicia/scout_import.py). No laptop timer.

echo "==> OK. Try: alicia health"
echo "    Alicia UI:    bash scripts/open-operator.sh  → http://127.0.0.1:8768/"
echo "    Reload Cursor MCP panels after install."
