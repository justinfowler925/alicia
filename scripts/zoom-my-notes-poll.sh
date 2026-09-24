#!/usr/bin/env bash
# Pull Justin's personal Zoom My Notes transcript into Alicia every five minutes.
set -uo pipefail

ALICIA_URL="${ALICIA_URL:-http://127.0.0.1:8768}"
DAYS="${ZOOM_POLL_DAYS:-7}"
OWNERS="${ZOOM_POLL_OWNERS:-justin}"
TIMEOUT="${ZOOM_MY_NOTES_TIMEOUT:-180}"

if ! curl -sf --max-time 5 "$ALICIA_URL/api/todos" >/dev/null; then
  echo "skip: Alicia down at $ALICIA_URL"
  exit 0
fi

owners_json=$(printf '%s' "$OWNERS" | awk -F, '{
  out=""
  for (i = 1; i <= NF; i++) {
    gsub(/^[ \t]+|[ \t]+$/, "", $i)
    if ($i != "") out = out (out == "" ? "" : ",") "\"" $i "\""
  }
  print out
}')
body=$(printf '{"days":%s,"owners":[%s]}' "$DAYS" "$owners_json")

response=$(curl -s --max-time "$TIMEOUT" -X POST \
  -H 'content-type: application/json' \
  -d "$body" "$ALICIA_URL/api/zoom/my-notes/poll")
rc=$?
if [[ $rc -ne 0 || -z "$response" ]]; then
  echo "warn: My Notes poll did not complete (curl rc=$rc); will retry"
  exit 0
fi

printf '%s' "$response" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("warn: unparseable My Notes response")
    raise SystemExit(0)
if d.get("detail"):
    print("warn:", d["detail"])
    raise SystemExit(0)
print(
    "Done. created={created} pending={pending} recent={recent} listed={listed} "
    "unchanged={unchanged} errors={errors}".format(
        created=d.get("created"), pending=d.get("pending"),
        recent=d.get("notes_recent"), listed=d.get("notes_listed"),
        unchanged=d.get("skipped_unchanged"), errors=len(d.get("errors") or []),
    )
)
for result in d.get("results") or []:
    print("  +", result.get("state"), (result.get("note_name") or "")[:100])
for err in d.get("errors") or []:
    print("  !", str(err)[:160])
'
exit 0
