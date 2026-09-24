#!/usr/bin/env bash
# Re-vendor the shine token layer into alicia/static/shine-tokens.css.
#
# Alicia has no build step, so the tokens are copied in rather than imported.
# The copy's own header has named this script since it was created; the script
# did not exist, which is why the vendored layer sat stale behind shine and the
# type tokens took a second pass to arrive. A documented step nobody can run is
# not a step.
#
# Usage: scripts/sync-shine-tokens.sh [--check]
#   --check  exit 1 if the vendored copy is stale (for CI / shine's doctor)
#
# A plain run builds shine first. Copying dist/ without building copies whatever
# was last built in that checkout, which is the same staleness this script exists
# to remove, one directory over. --check deliberately does not build: it answers
# "is the committed copy current?" and must not change what it is measuring.
set -euo pipefail

SHINE="${SHINE_DIR:-$HOME/Projects/shine}"
SRC="$SHINE/tokens/dist/personal/artifact.css"
DEST="$(cd "$(dirname "$0")/.." && pwd)/alicia/static/shine-tokens.css"
MODE="${1:-}"

if [[ "$MODE" != "--check" ]]; then
  if [[ ! -d "$SHINE/tokens" ]]; then
    echo "no shine checkout at $SHINE (set SHINE_DIR)" >&2
    exit 1
  fi
  echo "building shine tokens in $SHINE/tokens" >&2
  ( cd "$SHINE/tokens" && npm run --silent build >&2 )
fi

if [[ ! -f "$SRC" ]]; then
  echo "shine token dist not found at $SRC" >&2
  echo "run: (cd $SHINE/tokens && npm ci && npm run build)" >&2
  exit 1
fi

header() {
  cat <<'EOF'
/* -------------------------------------------------------
 * Vendored from @shine/personal (tokens/dist/personal/artifact.css).
 * DO NOT EDIT — re-vendor with scripts/sync-shine-tokens.sh when shine
 * rebuilds. Alicia has no build step, so the token layer is copied in
 * rather than imported; that copy is the only reason this file exists.
 * ------------------------------------------------------- */
EOF
}

# The vendored file carries Alicia's own header instead of shine's generated one,
# so strip shine's leading comment block and prepend ours.
body() { awk 'BEGIN{s=0} s==1{print} /^ \* -+ \*\/$/{if(s==0){s=1}}' "$SRC" | sed '/./,$!d'; }

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
{ header; body; } > "$TMP"

# The count is the smoke test, and it runs before anything is written. A token
# layer that emits nothing still copies cleanly, and a stylesheet whose every
# var() resolves to nothing renders as unstyled rather than as an error — so the
# failure would surface as "the UI looks broken", not as a bad vendoring step.
n="$(grep -c -- '^  --shine-' "$TMP" || true)"
if (( n < 40 )); then
  echo "only $n custom properties in the generated copy — check the shine build" >&2
  exit 1
fi

if [[ "$MODE" == "--check" ]]; then
  if diff -q "$TMP" "$DEST" >/dev/null 2>&1; then
    echo "shine-tokens.css in sync with $SRC ($n tokens)"
    exit 0
  fi
  echo "STALE: alicia/static/shine-tokens.css differs from shine's dist" >&2
  diff -u "$DEST" "$TMP" | head -40 >&2
  echo "fix: scripts/sync-shine-tokens.sh" >&2
  exit 1
fi

cp "$TMP" "$DEST"
echo "vendored $SRC -> alicia/static/shine-tokens.css ($n tokens)"
