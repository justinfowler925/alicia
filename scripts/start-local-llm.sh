#!/bin/zsh
# Start Alicia local MLX server (laptop, port 7901).
#
# Model id comes from config.yaml — the same file the Alicia client reads —
# so a swap like 14B→8B is one edit, not a three-file footgun that leaves the
# client asking for weights the server never loaded.
set -euo pipefail
APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# mlx + huggingface live in the shared checkout venv; the deployed worktree
# does not reinstall them. Override with ALICIA_ROOT if needed.
VENV_ROOT="${ALICIA_ROOT:-$HOME/Projects/alicia}"
LOG_DIR="${HOME}/.cursor/logs"
mkdir -p "$LOG_DIR"
# shellcheck disable=SC1091
source "$VENV_ROOT/.venv/bin/activate"

MODEL="$("$VENV_ROOT/.venv/bin/python" - <<PY
from pathlib import Path
from alicia.config import load_config

default = "mlx-community/Qwen3-8B-4bit"
for p in (Path("$APP_ROOT") / "config.yaml", Path.home() / "Projects/alicia" / "config.yaml"):
    if p.exists():
        cfg = load_config(p)
        llm = cfg.local_llm
        print((llm.model if llm and llm.model else None) or default)
        break
else:
    print(default)
PY
)"

export PYTHONUNBUFFERED=1
exec python -u -m mlx_lm server \
  --model "$MODEL" \
  --host 127.0.0.1 \
  --port 7901
