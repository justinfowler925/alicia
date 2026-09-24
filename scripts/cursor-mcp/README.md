# Cursor MCP helpers (laptop)

Tracked copies of the scripts that normally live under `~/.cursor/scripts/`.

| File | Role |
|------|------|
| `atlas-chat-mcp.py` | MCP chat — prefers Atlas6 `:8767`, falls back Atlas5 `:8766` / SSH |
| `atlas-chat-tunnel.sh` | Persistent SSH forward for Atlas5 `:8766` (`ControlMaster=no`) |

Sync to home after edits:

```bash
cp scripts/cursor-mcp/atlas-chat-mcp.py ~/.cursor/scripts/
cp scripts/cursor-mcp/atlas-chat-tunnel.sh ~/.cursor/scripts/
launchctl kickstart -k "gui/$(id -u)/com.clearspeed.atlas-chat-tunnel"
```

Alicia portfolio tunnel is separate: `scripts/alicia-tunnel.sh` → `:8767`.
