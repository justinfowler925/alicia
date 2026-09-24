#!/usr/bin/env python3
"""POST Zoom meeting assets into Alicia as captures.

The transport half of the AI Companion lane. Extraction, dedupe and the
idempotency ledger all live in `alicia.zoom_ingest` behind
`POST /api/zoom/ingest` — this script only moves already-fetched payloads over,
so it stays useful whichever thing does the fetching.

Fetching is the part that needs a credential, and Alicia does not have one. The
Zoom access proven to work today is the Claude Zoom connector, which is not
reachable from launchd; so the payloads arrive as files written by whatever ran
`get_meeting_assets`:

    # from a Claude session (or a scheduled Claude run) holding the connector
    python3 scripts/zoom_notes_to_alicia.py assets/*.json --execute

Transcripts are stripped before sending. They can be 200KB of "hold on, hold on"
and nothing downstream reads them.

Safe to run unattended: exits 0 when Alicia is down, the same convention as
`feed_zoom_to_alicia_notes.py`, so launchd does not spam.

Examples:
  python3 scripts/zoom_notes_to_alicia.py meeting.json --dry-run
  cat assets.json | python3 scripts/zoom_notes_to_alicia.py - --execute
  python3 scripts/zoom_notes_to_alicia.py *.json --owners justin --execute
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_ALICIA = os.environ.get("ALICIA_URL", "http://127.0.0.1:8768").rstrip("/")


def load_payloads(sources: list[str]) -> list[dict[str, Any]]:
    """Read asset payloads from files (or `-` for stdin).

    Accepts a single asset object, a list of them, or a `{"meetings": [...]}`
    wrapper, because all three are shapes a caller naturally produces.
    """
    out: list[dict[str, Any]] = []
    for src in sources:
        raw = sys.stdin.read() if src == "-" else Path(src).read_text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"warn: {src}: not JSON ({exc})", file=sys.stderr)
            continue
        if isinstance(data, dict) and isinstance(data.get("meetings"), list):
            data = data["meetings"]
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            if not item.get("meeting_uuid"):
                print(f"warn: {src}: payload has no meeting_uuid, skipping", file=sys.stderr)
                continue
            out.append(strip_transcript(item))
    return out


def strip_transcript(assets: dict[str, Any]) -> dict[str, Any]:
    """Drop the transcript — nothing downstream reads it and it dwarfs the rest."""
    trimmed = dict(assets)
    notes = trimmed.get("my_notes")
    if isinstance(notes, dict):
        notes = dict(notes)
        notes.pop("transcript", None)
        trimmed["my_notes"] = notes
    return trimmed


def alicia_up(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/todos", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def post_ingest(base: str, body: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{base}/api/zoom/ingest",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+", help="asset JSON files, or - for stdin")
    ap.add_argument("--alicia-url", default=DEFAULT_ALICIA)
    ap.add_argument("--owners", default="", help="comma-separated owner substrings to keep")
    ap.add_argument(
        "--mode",
        default="notes",
        choices=("notes", "both"),
        help="notes: My Notes action items, falling back to the summary (default). "
        "both: read both sections and accept the overlap.",
    )
    ap.add_argument("--stage", default="Captured")
    ap.add_argument(
        "--skip-ingested",
        action="store_true",
        help="skip meetings already ingested at all, rather than diffing their items",
    )
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    execute = args.execute and not args.dry_run

    payloads = load_payloads(args.sources)
    if not payloads:
        print("nothing to ingest")
        return 0

    if not alicia_up(args.alicia_url):
        # Not a failure: the laptop daemon is simply not up. Next run picks it up.
        print(f"skip: Alicia not reachable at {args.alicia_url}")
        return 0

    body: dict[str, Any] = {
        "meetings": payloads,
        "mode": args.mode,
        "stage": args.stage,
        "dry_run": not execute,
        "skip_ingested": args.skip_ingested,
    }
    owners = [o.strip() for o in args.owners.split(",") if o.strip()]
    if owners:
        body["owners"] = owners

    try:
        result = post_ingest(args.alicia_url, body)
    except urllib.error.HTTPError as exc:
        print(f"error: Alicia rejected the batch: {exc.code} {exc.read()[:300]!r}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    verb = "created" if execute else "would create"
    for res in result.get("results") or []:
        if res.get("skipped_meeting"):
            print(f"  [skip] {res.get('meeting_uuid')} already ingested")
            continue
        print(f"  [{res.get('topic')}] {verb}={res.get('created')} dup={res.get('skipped_duplicate')}")
        for item in res.get("items") or []:
            print(f"     - {item.get('text', '')[:110]}")
    for err in result.get("errors") or []:
        print(f"  [error] {err}", file=sys.stderr)

    print(
        f"Done. {verb}={result.get('created')} "
        f"skipped_duplicate={result.get('skipped_duplicate')} "
        f"meetings={result.get('meetings_processed')}"
    )
    # A partial failure is still worth a non-zero exit so a wrapper can log it.
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
