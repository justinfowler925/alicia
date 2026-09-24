"""Turn a raw capture into something workable.

Justin says a paragraph; the queue needs a line. This drafts a title, a one-line
summary and the list of things still missing, then parks the item in `Refining`
for him to confirm. It never promotes its own guess to `Ready` — a summariser
that decides is a summariser you have to audit.

Two invariants, both load-bearing:

**Capture never blocks on this.** The row is written first and refined after. A
dead router, a timeout, a model returning prose instead of JSON — all of them
leave a stored capture with a deterministic title, never a lost one. The pad has
to stay usable in two seconds or it stops getting used, and that matters more
than a pretty title.

**The verbatim text survives.** `raw` is never rewritten. Every draft is
reversible because the thing he actually said is still on the row.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import AliciaCfg
from .brain import BrainError, complete
from .todos import SUMMARY_MAX, TITLE_MAX, Todo, TodoStore

_WS = re.compile(r"\s+")
# A capture usually opens with the thing itself and trails into context. Cut at
# the first hard stop so the title is the subject, not the first 90 characters.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\s+[—–]\s+|\s*\n+")

SYSTEM = (
    "You turn a raw spoken note into a work item. Reply with JSON only, no prose "
    "and no code fence.\n"
    "Keys, all required:\n"
    'title: an imperative line naming the work, at most 80 characters, no trailing period\n'
    'summary: one sentence of what it is and why it matters, at most 200 characters\n'
    'missing: array of short questions that must be answered before work can start; '
    "[] if the note is already actionable\n"
    "Rules:\n"
    "Use only facts present in the note. Never invent an owner, a date or a system.\n"
    "Keep identifiers exactly as written, including ticket ids like REV-409.\n"
    "If the note is already one clear line, reuse it as the title and return [] for missing.\n"
    "Ask at most three questions in missing, most important first."
)


@dataclass
class Draft:
    title: str
    summary: str
    missing: list[str]
    path: str  # "brain" or "fallback:<reason>" — how this draft was produced

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "missing": list(self.missing),
            "path": self.path,
        }


def fallback_title(raw: str) -> str:
    """A readable title with no model involved.

    Trims on a word boundary, never mid-word: a title cut mid-word reads as
    corruption, and there is no way to tell it apart from a truncated write.
    """
    text = _WS.sub(" ", (raw or "").strip())
    if not text:
        return ""
    first = _SENTENCE_END.split(text, maxsplit=1)[0].strip()
    first = first.rstrip(".,;:")
    if len(first) <= TITLE_MAX:
        return first
    cut = first[: TITLE_MAX - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return f"{cut.rstrip(',;:')}…"


def _parse(reply: str) -> Draft | None:
    """Pull the JSON object out of a reply that may carry a fence or a preamble."""
    text = (reply or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None

    title = _WS.sub(" ", str(obj.get("title") or "").strip()).rstrip(".")[:TITLE_MAX]
    if not title:
        return None
    summary = _WS.sub(" ", str(obj.get("summary") or "").strip())[:SUMMARY_MAX]
    raw_missing = obj.get("missing")
    missing: list[str] = []
    if isinstance(raw_missing, list):
        for item in raw_missing[:3]:
            q = _WS.sub(" ", str(item).strip())
            if q:
                missing.append(q[:200])
    return Draft(title=title, summary=summary, missing=missing, path="brain")


def draft(cfg: AliciaCfg, raw: str) -> Draft:
    """Draft a title/summary/questions for one capture. Always returns."""
    text = _WS.sub(" ", (raw or "").strip())
    if not text:
        return Draft(title="", summary="", missing=[], path="fallback:empty")

    # Already a single short line — there is nothing to summarise, and calling a
    # model to retype it is how a fast pad becomes a slow one.
    if len(text) <= TITLE_MAX and "\n" not in (raw or ""):
        return Draft(title=text.rstrip("."), summary="", missing=[], path="fallback:already-short")

    try:
        reply = complete(
            cfg,
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
            allow_alternate=True,
        )
    except BrainError as exc:
        return Draft(
            title=fallback_title(text),
            summary="",
            missing=[],
            path=f"fallback:{type(exc).__name__}",
        )
    except Exception:  # a refiner may never be the reason a capture is lost
        return Draft(title=fallback_title(text), summary="", missing=[], path="fallback:error")

    parsed = _parse(reply)
    if parsed is None:
        return Draft(title=fallback_title(text), summary="", missing=[], path="fallback:unparsed")
    return parsed


def refine_todo(cfg: AliciaCfg, store: TodoStore, todo_id: str) -> Todo | None:
    """Draft against the stored `raw` and move the item to Refining."""
    current = store.get(todo_id)
    if current is None:
        return None
    d = draft(cfg, current.raw or current.text)
    if not d.title:
        return current
    return store.refine(todo_id, title=d.title, summary=d.summary, missing=d.missing)


def refine_backlog(cfg: AliciaCfg, store: TodoStore, limit: int = 25) -> dict[str, Any]:
    """Refine the oldest unrefined captures.

    Sequential on purpose. Two cloud backends, not a local router — still
    don't fire a batch concurrently or a timeout storm eats the backlog.
    """
    done, paths = [], {}
    for todo in store.needing_refinement(limit=limit):
        d = draft(cfg, todo.raw or todo.text)
        paths[d.path] = paths.get(d.path, 0) + 1
        if not d.title:
            continue
        store.refine(todo.id, title=d.title, summary=d.summary, missing=d.missing)
        done.append(todo.id)
    return {"refined": len(done), "ids": done, "paths": paths}
