#!/usr/bin/env bash
# Ask Alicia to pull new Zoom meeting summaries into the Inbox. Laptop only.
#
# The work happens inside the daemon (POST /api/zoom/poll) rather than here, so
# the ledger, the dedupe and the live push to the screen are all one code path
# with the endpoint a human can call by hand. This wrapper only decides WHEN.
#
# Deliberately quiet: a stopped daemon or a Zoom hiccup exits 0 so launchd keeps
# the job loaded and simply tries again next interval.
set -uo pipefail

ALICIA_URL="${ALICIA_URL:-http://127.0.0.1:8768}"
DAYS="${ZOOM_POLL_DAYS:-7}"
OWNERS="${ZOOM_POLL_OWNERS:-justin}"
# The first run on a cold ledger resolves every meeting in the window, which
# takes minutes; later runs skip everything already decided.
TIMEOUT="${ZOOM_POLL_TIMEOUT:-900}"

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
  -d "$body" "$ALICIA_URL/api/zoom/poll")
rc=$?

if [[ $rc -ne 0 || -z "$response" ]]; then
  echo "warn: zoom poll did not complete (curl rc=$rc); will retry next interval"
  exit 0
fi

# One line per run so the log reads as a history rather than a dump.
# No escaped quotes below: this block is a single-quoted shell string, and a
# backslash-quote inside an f-string reaches python as a line continuation and
# dies with a SyntaxError instead of reporting the run.
printf '%s' "$response" | python3 -c '
import json, sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("warn: unparseable response from Alicia")
    raise SystemExit(0)

detail = d.get("detail")
if detail:
    print("warn:", detail)
    raise SystemExit(0)

w = d.get("window") or {}
fields = [
    ("created", d.get("created")),
    ("dup", d.get("skipped_duplicate")),
    ("mine", d.get("mine")),
    ("listed", d.get("summaries_listed")),
    ("resolved_before", d.get("already_resolved")),
    ("window", str(w.get("from")) + ".." + str(w.get("to"))),
    ("errors", len(d.get("errors") or [])),
]
print("Done. " + " ".join(k + "=" + str(v) for k, v in fields))
for res in d.get("results") or []:
    for item in res.get("items") or []:
        print("  +", (item.get("text") or "")[:110])
for err in d.get("errors") or []:
    print("  !", str(err)[:160])
'
exit 0
