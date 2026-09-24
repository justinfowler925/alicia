#!/usr/bin/env python3
"""Feed Justin's Zoom meeting action items into Alicia Notes (Inbox).

Laptop-local. Polls Salesforce Meeting_Notes__c for Extracted Zoom notes,
filters to Justin-hosted meetings or items Atlas assigned to Justin, and
POSTs one Alicia Note per action item for pick/choose → Promote.

Safe when Alicia is down (exit 0). Idempotent via ~/.alicia/zoom_notes_ledger.jsonl.

Examples:
  python3 feed_zoom_to_alicia_notes.py --dry-run
  python3 feed_zoom_to_alicia_notes.py --since-days 30 --execute
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

JUSTIN_EMAIL = "justin.fowler@clearspeed.com"
DEFAULT_ALICIA = os.environ.get("ALICIA_URL", "http://127.0.0.1:8768").rstrip("/")
DEFAULT_ORG = os.environ.get("SF_TARGET_ORG", "prod-admin")
_HOME = Path.home() / ".alicia"
if not _HOME.exists() and (Path.home() / ".brutus").is_dir():
    _HOME = Path.home() / ".brutus"  # pre-cutover legacy home
DEFAULT_LEDGER = _HOME / "zoom_notes_ledger.jsonl"


def parse_items(text: str) -> list:
    """Same span-walk as extract_action_items.parse_items (nested tags-safe)."""
    if not text:
        return []
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    best: list = []
    i = 0
    while i < len(text):
        if text[i] != "[":
            i += 1
            continue
        depth = 0
        end = None
        for j in range(i, len(text)):
            if text[j] == "[":
                depth += 1
            elif text[j] == "]":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end is None:
            break
        candidate = text[i : end + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list) and (
                not parsed or isinstance(parsed[0], dict)
            ):
                best = parsed
        except Exception:
            pass
        i = end + 1
    return best


def sf_query(org: str, soql: str) -> list:
    env = {**os.environ, "SF_SKIP_NEW_VERSION_CHECK": "1"}
    out = subprocess.run(
        ["sf", "data", "query", "-o", org, "--json", "-q", soql],
        capture_output=True, text=True, env=env,
    )
    if out.returncode != 0:
        raise SystemExit(f"SOQL failed: {out.stderr or out.stdout}")
    raw = out.stdout
    start = raw.find("{")
    data = json.loads(raw[start:])
    return data.get("result", {}).get("records", [])


def alicia_up(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/todos", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def post_todo(base: str, text: str, tags: str = "zoom,auto") -> dict:
    body = json.dumps({"text": text, "tags": tags, "lane": "Inbox"}).encode()
    req = urllib.request.Request(
        f"{base}/api/todos",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def load_ledger(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                keys.add(json.loads(line)["key"])
            except Exception:
                continue
    return keys


def append_ledger(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def item_key(meeting_id: str, idx: int, action: str) -> str:
    h = hashlib.sha1(action.strip().lower().encode()).hexdigest()[:12]
    return f"{meeting_id}:{idx}:{h}"


def items_from_note(rec: dict) -> list[dict]:
    """Return [{idx, action, owner_email}] for a Meeting_Notes row."""
    raw = rec.get("Action_Items_Raw__c") or ""
    parsed = [x for x in parse_items(raw) if isinstance(x, dict)]
    out = []
    if parsed:
        for idx, it in enumerate(parsed[:3]):
            action = (str(it.get("action") or "")).strip()
            if not action:
                continue
            out.append({
                "idx": idx,
                "action": action[:500],
                "owner_email": (str(it.get("owner_email") or "")).strip().lower(),
            })
        return out
    # Fall back to structured next-step fields
    for k in (1, 2, 3):
        action = (rec.get(f"Next_Step_{k}__c") or "").strip()
        if not action:
            continue
        out.append({"idx": k - 1, "action": action[:500], "owner_email": ""})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", default=DEFAULT_ORG)
    ap.add_argument("--since-days", type=int, default=14)
    ap.add_argument("--alicia-url", default=DEFAULT_ALICIA)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--justin-email", default=JUSTIN_EMAIL)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    execute = args.execute and not args.dry_run

    if not alicia_up(args.alicia_url):
        print(f"skip: Alicia not reachable at {args.alicia_url}")
        sys.exit(0)

    justin = args.justin_email.lower()
    soql = (
        "SELECT Id, Name, Host_Email__c, Action_Items_Raw__c, "
        "Next_Step_1__c, Next_Step_2__c, Next_Step_3__c "
        "FROM Meeting_Notes__c "
        "WHERE Zoom_Meeting_UUID__c != null "
        "AND Action_Items_Status__c = 'Extracted' "
        f"AND CreatedDate = LAST_N_DAYS:{args.since_days} "
        "ORDER BY CreatedDate DESC"
    )
    if args.limit:
        soql += f" LIMIT {args.limit}"

    recs = sf_query(args.org, soql)
    seen = load_ledger(args.ledger)
    created = 0
    skipped = 0

    for rec in recs:
        host = (rec.get("Host_Email__c") or "").lower()
        host_is_justin = host == justin
        name = rec.get("Name") or rec["Id"]
        for it in items_from_note(rec):
            owner = it["owner_email"]
            action = it["action"].strip()
            # Drop LLM placeholder / schema leakage
            if not action or action.lower() in {
                "<imperative phrase>",
                "imperative phrase",
                "null",
                "n/a",
            }:
                continue
            if action.startswith("<") and action.endswith(">"):
                continue
            # Justin-hosted → all items; otherwise only Justin-owned.
            if not (host_is_justin or owner == justin):
                continue
            key = item_key(rec["Id"], it["idx"], action)
            if key in seen:
                skipped += 1
                continue
            text = f"{action} — {name} ({rec['Id']})"
            if not execute:
                print(f"[dry-run] {text}")
                created += 1
                continue
            todo = post_todo(args.alicia_url, text)
            entry = {
                "key": key,
                "meeting_note_id": rec["Id"],
                "action": action,
                "todo_id": todo.get("id"),
                "host_email": host,
                "owner_email": owner,
            }
            append_ledger(args.ledger, entry)
            seen.add(key)
            created += 1
            print(f"[OK] {text[:120]}")

    mode = "posted" if execute else "would-post"
    print(f"Done. {mode}={created} skipped_ledger={skipped} notes_scanned={len(recs)}")


if __name__ == "__main__":
    main()
