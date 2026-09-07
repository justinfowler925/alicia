#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT"

"$PY" -m ruff check \
  brutus/workflow_control.py \
  brutus/workflow_cli.py \
  brutus/workflow_http.py \
  tests/test_workflow_control.py \
  tests/test_workflow_http.py

# The repository has a measured historical lint backlog. Keep every touched
# integration seam free of syntax/undefined-name failures without pretending
# that unrelated pre-existing style findings are green.
"$PY" -m ruff check --select E9,F63,F7,F82 \
  brutus/__main__.py \
  brutus/canon/models.py \
  brutus/canon/state_machine.py \
  brutus/canon/store.py \
  brutus/mcp_server.py \
  brutus/security.py \
  brutus/server.py \
  tests/test_mcp.py

bash -n scripts/deploy.sh scripts/install-brutus.sh
