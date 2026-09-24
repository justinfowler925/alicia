#!/usr/bin/env bash
# Download configured local LLM weights and print mlx_lm.server startup hints.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="${ROOT}/.venv"
PY="${VENV}/bin/python"
PIP="${VENV}/bin/pip"

if [[ ! -x "$PY" ]]; then
  python3 -m venv "$VENV"
fi

"$PIP" install -q -U pip
"$PIP" install -q -e ".[dev]"
"$PIP" install -q mlx-lm huggingface_hub pyyaml

read -r MODEL PORT <<< "$("$PY" - <<'PY'
from pathlib import Path
from alicia.config import load_config

cfg = load_config(Path.home() / "Projects/alicia/config.yaml")
llm = cfg.local_llm
model = llm.model if llm else "mlx-community/Qwen3-8B-4bit"
port = "7901"
if llm and llm.router_url.rstrip("/").endswith(":7901"):
    port = "7901"
elif llm and ":" in llm.router_url.split("//", 1)[-1]:
    port = llm.router_url.rsplit(":", 1)[-1]
print(model, port)
PY
)"

LOG="${ROOT}/.local-model-download.log"
echo "==> Downloading ${MODEL} (log: ${LOG})"
nohup "$PY" -c "
from huggingface_hub import snapshot_download
path = snapshot_download(repo_id='${MODEL}')
print('Cache ready:', path)
" >"$LOG" 2>&1 &
DL_PID=$!
echo "    Background PID ${DL_PID} — tail -f ${LOG}"

cat <<EOF

When download finishes, start the OpenAI-compatible router on the laptop:

  ${VENV}/bin/python -m mlx_lm.server \\
    --model ${MODEL} \\
    --host 127.0.0.1 \\
    --port ${PORT}

Then enable in config.yaml:

  local_llm:
    enabled: true
    router_url: "http://127.0.0.1:${PORT}"
    model: "${MODEL}"

Health check:

  alicia llm-health

EOF
