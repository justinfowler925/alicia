#!/usr/bin/env bash
# Feed Justin Zoom action items into Alicia Notes. Laptop only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export SF_SKIP_NEW_VERSION_CHECK=1
export SF_USE_GENERIC_UNIX_KEYCHAIN=true
export SFDX_USE_GENERIC_UNIX_KEYCHAIN=true
export SF_TARGET_ORG="${SF_TARGET_ORG:-prod-admin}"
export ALICIA_URL="${ALICIA_URL:-http://127.0.0.1:8768}"
SINCE="${ZOOM_ALICIA_SINCE_DAYS:-14}"

# Quiet exit when Alicia UI is down — launchd should not spam.
if ! curl -sf --max-time 2 "$ALICIA_URL/api/todos" >/dev/null; then
  echo "skip: Alicia down at $ALICIA_URL"
  exit 0
fi

# Soft-fail SF auth flakes (Mac keychain vs launchd) so the agent stays loaded.
set +e
python3 "$ROOT/scripts/feed_zoom_to_alicia_notes.py" \
  --org "$SF_TARGET_ORG" \
  --since-days "$SINCE" \
  --alicia-url "$ALICIA_URL" \
  --execute
rc=$?
set -e
if [[ $rc -ne 0 ]]; then
  echo "warn: zoom→alicia feed exited $rc (will retry next interval)"
  exit 0
fi
exit 0