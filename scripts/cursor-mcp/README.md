# Cursor MCP helpers (laptop)

Tracked copies of the scripts that normally live under `~/.cursor/scripts/`.

| File | Role |
|------|------|
| `atlas-chat-mcp.py` | MCP chat — prefers Atlas6 `:8767`, falls back Atlas5 `:8766` / SSH |
| `atlas-chat-tunnel.sh` | Persistent SSH forward for Atlas5 `:8766` (`ControlMaster=no`) |
| `studio-local-model-tunnel.sh` | Persistent SSH forward for Studio Rapid-MLX `:7900` (OpenAI-compatible) |

Sync to home after edits:

```bash
cp scripts/cursor-mcp/atlas-chat-mcp.py ~/.cursor/scripts/
cp scripts/cursor-mcp/atlas-chat-tunnel.sh ~/.cursor/scripts/
cp scripts/cursor-mcp/studio-local-model-tunnel.sh ~/.cursor/scripts/
launchctl kickstart -k "gui/$(id -u)/com.clearspeed.atlas-chat-tunnel"
launchctl kickstart -k "gui/$(id -u)/com.clearspeed.studio-local-model-tunnel"
```

Brutus portfolio tunnel is separate: `scripts/brutus-tunnel.sh` → `:8767`.

### Studio local model from Cursor

Always-on LaunchAgent: `com.clearspeed.studio-local-model-tunnel`  
Laptop URL: `http://127.0.0.1:7900/v1`  
Model id: `qwen3.8-27b` (resident: `Qwen3-Coder-30B-A3B-Instruct-8bit`)

In Cursor → Settings → Models: enable OpenAI-compatible / override base URL to that path, use any non-empty API key, model name `qwen3.8-27b`.
