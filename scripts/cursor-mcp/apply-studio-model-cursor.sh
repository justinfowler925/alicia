#!/bin/zsh
# Apply Studio local-model BYOK settings to Cursor's state.vscdb.
# Cursor must be fully quit (Cmd+Q) while this runs, or in-memory state will overwrite it.
# Architecture: Cursor cloud → Tailscale Funnel → Studio Caddy :8080 → Rapid-MLX :7900
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

DB="${CURSOR_STATE_DB:-$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb}"
APP_KEY='src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl.persistentStorage.applicationUser'
BASE_URL="${STUDIO_MODEL_BASE_URL:-https://justins-mac-studio-1.tailbaa084.ts.net/v1}"
MODEL_IDS=("qwen3.8-27b" "mlx-community/Qwen3-Coder-30B-A3B-Instruct-8bit")

if pgrep -x Cursor >/dev/null 2>&1; then
  print -u2 -- "ERROR: Cursor is still running. Quit Cursor (Cmd+Q) first, then re-run."
  exit 2
fi

if [[ ! -f "$DB" ]]; then
  print -u2 -- "ERROR: missing $DB"
  exit 1
fi

TOKEN="$(op read 'op://Atlas/LOCAL_MODEL_GATEWAY_TOKEN/credential')"
if [[ -z "${TOKEN:-}" ]]; then
  print -u2 -- "ERROR: could not read op://Atlas/LOCAL_MODEL_GATEWAY_TOKEN/credential"
  exit 1
fi

# Prove the Funnel path before writing Cursor state.
HTTP_CODE="$(curl -sS -m 15 -o /tmp/studio-model-verify.json -w '%{http_code}' \
  -H "Authorization: Bearer ${TOKEN}" \
  "${BASE_URL}/models")"
if [[ "$HTTP_CODE" != "200" ]]; then
  print -u2 -- "ERROR: ${BASE_URL}/models returned HTTP ${HTTP_CODE}"
  head -c 200 /tmp/studio-model-verify.json >&2 || true
  print -u2 --
  exit 1
fi

BACKUP="${DB}.bak-studio-model-$(date +%Y%m%d-%H%M%S)"
cp -p "$DB" "$BACKUP"

python3 - "$DB" "$APP_KEY" "$BASE_URL" "$TOKEN" "${MODEL_IDS[@]}" <<'PY'
import json, sqlite3, sys
from pathlib import Path

db_path, app_key, base_url, token, *models = sys.argv[1:]
conn = sqlite3.connect(db_path)
raw = conn.execute("SELECT value FROM ItemTable WHERE key=?", (app_key,)).fetchone()
if not raw:
    raise SystemExit(f"missing key {app_key}")
data = json.loads(raw[0])
data["openAIBaseUrl"] = base_url
data["useOpenAIKey"] = True
ai = data.setdefault("aiSettings", {})
user_models = list(ai.get("userAddedModels") or [])
enabled = list(ai.get("modelOverrideEnabled") or [])
for mid in models:
    if mid not in user_models:
        user_models.append(mid)
    if mid not in enabled:
        enabled.append(mid)
ai["userAddedModels"] = user_models
ai["modelOverrideEnabled"] = enabled
data["aiSettings"] = ai

# FireConnect-compatible plaintext key (Cursor may migrate to secret:// on launch).
conn.execute(
    "INSERT INTO ItemTable(key, value) VALUES(?, ?) "
    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
    ("cursorAuth/openAIKey", token),
)
conn.execute(
    "UPDATE ItemTable SET value=? WHERE key=?",
    (json.dumps(data, separators=(",", ":"), ensure_ascii=False), app_key),
)
conn.commit()
conn.close()
print(f"wrote openAIBaseUrl={base_url}")
print(f"wrote userAddedModels={user_models}")
print("wrote cursorAuth/openAIKey (plaintext, from Atlas LOCAL_MODEL_GATEWAY_TOKEN)")
PY

print -u2 -- "backup: $BACKUP"
print -u2 -- "OK — reopen Cursor, pick model qwen3.8-27b, click Verify if shown."
