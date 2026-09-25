"""Salesforce Meeting_Notes__c action items → Alicia notes.

Shared by scripts/feed_zoom_to_alicia_notes.py and the Scout import
(alicia/scout_import.py); the deployed runtime does not ship scripts/.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


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
