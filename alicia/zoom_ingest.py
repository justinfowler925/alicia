"""Zoom AI Companion notes → the capture pad.

There is already a Zoom lane into Alicia: `scripts/feed_zoom_to_alicia_notes.py`
polls Salesforce `Meeting_Notes__c`, which the org-wide Apex pipeline fills from
`recording.completed` webhooks. That lane works and this module does not replace
it — it covers the meetings that lane structurally cannot see.

The gap is cloud recording. `Meeting_Notes__c` only ever gets a row when a
meeting was cloud-recorded, and Justin does not cloud-record: `recordings_list`
over a month returns zero. His meetings carry AI Companion output instead, so the
hourly job scans 600+ notes and posts nothing, every hour, correctly. What it is
scanning is other people's recordings where an item happens to name him.

Zoom puts commitments in two places on those meetings, both reachable through
`get_meeting_assets` without any recording:

    my_notes.content_markdown        "## Action Items", one bullet per item,
                                     owner in a bold prefix.
    meeting_summary.summary_markdown "## Next steps", owners as "###" subheads,
                                     every bullet carrying a tasks.zoom.us link.

Both shapes are parsed here, but only one is used per meeting by default — they
are two AI restatements of the same conversation, and blending them duplicated a
third of a real meeting's items. `extract_items` explains why the overlap is
ranked rather than fuzzy-matched away. Items land at stage `Captured` with
`source="zoom"`, which puts them in the Inbox for routing and lets the refine
sweeper draft titles the same way it does for speech.

Three things this module is careful about:

1.  **It never reshapes `todos`.** Bookkeeping lives in its own tables.
    Adding a column to a shared table is what blinded the deployed daemon on
    2026-08-08 (see `Todo.from_row` and `paths.may_migrate_shared_schema`); new
    tables are genuinely additive because no older reader selects from them.

2.  **Zoom's "nothing to report" prose is not a task.** "No action items
    assigned." and "Next steps were not generated due to insufficient
    transcript." arrive as ordinary bullets, so an unfiltered parse invents work
    nobody was assigned.

3.  **Transport is somebody else's problem.** `ingest_assets` takes an
    already-fetched payload, so the same extraction serves a Claude session
    holding the Zoom connector today and an in-process poller if Zoom
    server-to-server credentials ever exist. No Zoom client lives here.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import state_path
from .todos import TodoStore

#: What Zoom writes into a bullet list when the AI found nothing. Matched on the
#: normalised prefix so trailing wording ("...; conversation ended before any
#: agenda was reached.") does not need enumerating.
_PLACEHOLDER_PREFIXES = (
    "no action items",
    "no pending items",
    "no business questions",
    "no next steps",
    "no decisions",
    "no items identified",
    "none identified",
    "next steps were not generated",
    "not applicable",
    "n a",
)

#: `text[https://tasks.zoom.us?...](https://tasks.zoom.us?...)` — label and href
#: are the same URL, so the whole tail is noise.
_TASK_LINK_RE = re.compile(r"\[https?://[^\]]*\]\([^)]*\)")
_ANY_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_H2_RE = re.compile(r"^\s*##\s+(.*?)\s*$")
_H3_RE = re.compile(r"^\s*###\s+(.*?)\s*$")
#: `**Owner:** text` and `**Owner**: text` — Zoom emits both.
_BOLD_OWNER_COLON_RE = re.compile(r"^\*\*(?P<owner>[^*]{1,60}?):\*\*\s*(?P<rest>.*)$")
_BOLD_OWNER_RE = re.compile(r"^\*\*(?P<owner>[^*]{1,60}?)\*\*\s*:\s*(?P<rest>.*)$")

#: Headings under "## Next steps" that name a bucket rather than a person; their
#: bullets name their own owners inline ("Team (Jimmy, Patrick): ...").
_BUCKET_HEADINGS = {"collaboration", "team", "group", "everyone", "all"}

MAX_TODO_TEXT = 500
SOURCE = "zoom"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalise(text: str) -> str:
    norm = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return re.sub(r"\s+", " ", norm).strip()


def _is_placeholder(text: str) -> bool:
    norm = _normalise(text)
    return not norm or any(norm.startswith(p) for p in _PLACEHOLDER_PREFIXES)


def _clean(text: str) -> str:
    """Strip Zoom's task deep-links and markdown emphasis, collapse whitespace."""
    text = _TASK_LINK_RE.sub("", text)
    text = _ANY_MD_LINK_RE.sub(r"\1", text)  # keep the label, drop the href
    text = text.replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", text).strip().strip(".,;").strip()


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")[:limit].strip("-")


#: How to combine the two sections. `"notes"` uses My Notes' Action Items when it
#: has any and falls back to the summary's Next steps otherwise; `"both"` reads
#: both and dedupes only on identical text.
SOURCE_MODES = ("notes", "both")
DEFAULT_SOURCE_MODE = "notes"


@dataclass(frozen=True)
class ActionItem:
    """One commitment lifted out of a meeting."""

    owner: str
    text: str
    source: str  # "action_items" | "next_steps"

    @property
    def key(self) -> str:
        """Identity for dedupe — insensitive to owner formatting and punctuation."""
        return hashlib.sha256(
            f"{_normalise(self.owner)}|{_normalise(self.text)}".encode()
        ).hexdigest()[:16]

    def as_todo_text(self) -> str:
        body = f"{self.owner}: {self.text}" if self.owner else self.text
        return body[:MAX_TODO_TEXT]


def _h2_sections(markdown: str) -> dict[str, str]:
    """Map each `## Heading` (lowercased) to its body."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in (markdown or "").splitlines():
        m = _H2_RE.match(line)
        if m:
            current = m.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def _find_section(markdown: str, *names: str) -> str:
    sections = _h2_sections(markdown)
    for name in names:
        for heading, body in sections.items():
            if heading == name or heading.startswith(name):
                return body
    return ""


def parse_action_items(my_notes_markdown: str) -> list[ActionItem]:
    """Parse `## Action Items` out of My Notes (`- **Owner**: text`)."""
    items: list[ActionItem] = []
    for line in _find_section(my_notes_markdown, "action items", "action item").splitlines():
        m = _BULLET_RE.match(line)
        if not m:
            continue
        raw = m.group(1).strip()
        owner, rest = "", raw
        for pattern in (_BOLD_OWNER_COLON_RE, _BOLD_OWNER_RE):
            om = pattern.match(raw)
            if om and om.group("rest").strip():
                owner, rest = _clean(om.group("owner")), om.group("rest")
                break
        text = _clean(rest)
        if _is_placeholder(text):
            continue
        items.append(ActionItem(owner=owner, text=text, source="action_items"))
    return items


def parse_next_steps(summary_markdown: str) -> list[ActionItem]:
    """Parse `## Next steps` out of the AI summary (`### Owner` + bullets)."""
    items: list[ActionItem] = []
    owner = ""
    for line in _find_section(summary_markdown, "next steps", "next step").splitlines():
        h3 = _H3_RE.match(line)
        if h3:
            heading = _clean(h3.group(1))
            owner = "" if heading.lower() in _BUCKET_HEADINGS else heading
            continue
        m = _BULLET_RE.match(line)
        if not m:
            continue
        text = _clean(m.group(1))
        if _is_placeholder(text):
            continue
        items.append(ActionItem(owner=owner, text=text, source="next_steps"))
    return items


#: Words that join names in a shared owner ("Jimmy + Patrick", "Rob / Swapna").
_OWNER_JOINERS = {"+", "/", "&", "and", ",", "-"}


def _looks_like_owner(head: str) -> bool:
    """Whether a pre-colon fragment is a name rather than the start of a sentence.

    "Jimmy Gibson", "Rob / Swapna" and "Team (Jimmy, Patrick)" are owners.
    "Decide the following" is not, and treating it as one truncates the task to
    everything after the colon — which is how a commitment quietly loses its verb.
    Names are capitalised; sentences carry lowercase function words.
    """
    words = [w for w in head.replace("(", " ").replace(")", " ").split() if w]
    if not words or len(words) > 6:
        return False
    for word in words:
        if word.lower().strip(",") in _OWNER_JOINERS:
            continue
        letters = word.lstrip("(<[\"'")
        if not letters or not letters[0].isupper():
            return False
    return True


def parse_api_next_steps(next_steps: Any) -> list[ActionItem]:
    """Parse the REST API's flat next-steps list.

    `GET /meetings/{uuid}/meeting_summary` returns the same commitments the
    connector renders as markdown, but already flattened to
    `["Jimmy Gibson: Prepare and serve the demo…", …]`. The owner is the part
    before the first colon, and only when it looks like a name — plenty of real
    items contain a colon mid-sentence.
    """
    items: list[ActionItem] = []
    for entry in next_steps if isinstance(next_steps, list) else []:
        text = _clean(str(entry or ""))
        if _is_placeholder(text):
            continue
        owner = ""
        head, sep, rest = text.partition(":")
        if sep and rest.strip() and len(head) <= 60 and _looks_like_owner(head):
            owner, text = head.strip(), rest.strip()
        if _is_placeholder(text):
            continue
        items.append(ActionItem(owner=owner, text=text, source="next_steps"))
    return items


def extract_items(assets: dict[str, Any], *, mode: str = DEFAULT_SOURCE_MODE) -> list[ActionItem]:
    """Every commitment in one `get_meeting_assets` payload.

    The two sections are two AI restatements of the same meeting, so reading both
    duplicates most of it: the real ITC sync yielded 16 items of which 5 were one
    commitment written twice — "Share the Shine UX review skill with Francesco"
    and "Share the Shine skill agent with Francesco Crippa to assist with UX
    design challenges", among others.

    Fuzzy matching was measured on that meeting and rejected rather than tuned.
    Token-overlap similarity puts genuine duplicates at 0.136–0.385 and genuinely
    distinct items from 0.158 up: the bands overlap, so every threshold both keeps
    duplicates and silently merges separate work. Merging two real tasks into one
    card loses a commitment, which is worse than the duplicate it prevents.

    So the sections are ranked instead of blended. My Notes' "Action Items" is the
    curated layer — attributed, concise, and the document Justin actually keeps —
    and when it has items it is used alone. Only when it is empty or absent does
    the summary's "Next steps" stand in, which is what happens on meetings where
    nobody took notes. Pass ``mode="both"`` to read both and accept the overlap;
    the meeting's Zoom links go on every capture either way, so the section that
    was not used is one click from the card.
    """
    if mode not in SOURCE_MODES:
        raise ValueError(f"mode must be one of {SOURCE_MODES}")
    my_notes = assets.get("my_notes") or {}
    summary = assets.get("meeting_summary") or {}
    notes_items = parse_action_items(str(my_notes.get("content_markdown") or ""))
    # The REST lane supplies a flat list; the connector lane supplies markdown.
    step_items = parse_api_next_steps(assets.get("next_steps")) or parse_next_steps(
        str(summary.get("summary_markdown") or "")
    )

    if mode == "notes":
        return notes_items or step_items

    items = list(notes_items)
    seen = {_normalise(i.text) for i in items}
    for candidate in step_items:
        if _normalise(candidate.text) in seen:
            continue
        seen.add(_normalise(candidate.text))
        items.append(candidate)
    return items


class ZoomIngestStore:
    """Idempotency ledger for Zoom ingest.

    Its own tables, never a column on `todos` — see the module docstring.
    Defaults to the same state dir as everything else, so `ALICIA_STATE_DIR`
    isolates branch work here too.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else state_path("todos.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS zoom_meetings (
                    meeting_uuid TEXT PRIMARY KEY,
                    topic TEXT NOT NULL DEFAULT '',
                    start_time TEXT NOT NULL DEFAULT '',
                    first_ingested_at TEXT NOT NULL,
                    last_ingested_at TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0
                )"""
            )
            # Meetings resolved as somebody else's. The summaries API is
            # account-wide, so a poll sees ~50 meetings a month of which two are
            # his; deciding "not mine" costs a participants call, and without
            # remembering the verdict an hourly job pays it 24 times a day
            # forever. Attendance never changes retroactively, so once is enough.
            c.execute(
                """CREATE TABLE IF NOT EXISTS zoom_not_mine (
                    meeting_uuid TEXT PRIMARY KEY,
                    topic TEXT NOT NULL DEFAULT '',
                    checked_at TEXT NOT NULL
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS zoom_items (
                    meeting_uuid TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    todo_id TEXT NOT NULL DEFAULT '',
                    owner TEXT NOT NULL DEFAULT '',
                    origin TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (meeting_uuid, item_key)
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS zoom_my_notes (
                    note_id TEXT PRIMARY KEY,
                    note_name TEXT NOT NULL DEFAULT '',
                    created_time TEXT NOT NULL DEFAULT '',
                    modified_time TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    recap_todo_id TEXT NOT NULL DEFAULT '',
                    last_ingested_at TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0
                )"""
            )

    def seen_keys(self, meeting_uuid: str) -> set[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT item_key FROM zoom_items WHERE meeting_uuid=?", (meeting_uuid,)
            ).fetchall()
        return {r["item_key"] for r in rows}

    def record_item(self, meeting_uuid: str, item: ActionItem, todo_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO zoom_items "
                "(meeting_uuid, item_key, todo_id, owner, origin, created_at) VALUES (?,?,?,?,?,?)",
                (meeting_uuid, item.key, todo_id, item.owner, item.source, _now()),
            )

    def record_meeting(self, meeting_uuid: str, topic: str, start_time: str, new_items: int) -> None:
        now = _now()
        with self._conn() as c:
            c.execute(
                """INSERT INTO zoom_meetings
                     (meeting_uuid, topic, start_time, first_ingested_at, last_ingested_at, item_count)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(meeting_uuid) DO UPDATE SET
                     last_ingested_at=excluded.last_ingested_at,
                     item_count=zoom_meetings.item_count + excluded.item_count,
                     topic=excluded.topic,
                     start_time=excluded.start_time""",
                (meeting_uuid, topic, start_time, now, now, new_items),
            )

    def meetings(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM zoom_meetings ORDER BY last_ingested_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def my_note(self, note_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM zoom_my_notes WHERE note_id=?", (note_id,)
            ).fetchone()
        return dict(row) if row else None

    def record_my_note(
        self,
        *,
        note_id: str,
        note_name: str,
        created_time: str,
        modified_time: str,
        content_hash: str,
        recap_todo_id: str,
        item_count: int,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO zoom_my_notes
                     (note_id, note_name, created_time, modified_time, content_hash,
                      recap_todo_id, last_ingested_at, item_count)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(note_id) DO UPDATE SET
                     note_name=excluded.note_name,
                     created_time=excluded.created_time,
                     modified_time=excluded.modified_time,
                     content_hash=excluded.content_hash,
                     recap_todo_id=excluded.recap_todo_id,
                     last_ingested_at=excluded.last_ingested_at,
                     item_count=excluded.item_count""",
                (
                    note_id,
                    note_name,
                    created_time,
                    modified_time,
                    content_hash,
                    recap_todo_id,
                    _now(),
                    int(item_count),
                ),
            )

    def mark_not_mine(self, meeting_uuid: str, topic: str = "") -> None:
        self.mark_many_not_mine([(meeting_uuid, topic)])

    def mark_many_not_mine(self, pairs: list[tuple[str, str]]) -> int:
        """Record a batch of other people's meetings in one transaction.

        A cold poll resolves hundreds of them. One write each meant hundreds of
        separate transactions on the database the daemon is serving; the whole
        batch is a single commit instead.
        """
        if not pairs:
            return 0
        now = _now()
        with self._conn() as c:
            c.executemany(
                "INSERT OR IGNORE INTO zoom_not_mine (meeting_uuid, topic, checked_at) VALUES (?,?,?)",
                [(uuid, topic, now) for uuid, topic in pairs],
            )
        return len(pairs)

    def is_not_mine(self, meeting_uuid: str) -> bool:
        with self._conn() as c:
            return (
                c.execute(
                    "SELECT 1 FROM zoom_not_mine WHERE meeting_uuid=?", (meeting_uuid,)
                ).fetchone()
                is not None
            )

    def is_resolved(self, meeting_uuid: str) -> bool:
        """Already ingested, or already known to be somebody else's."""
        return self.is_ingested(meeting_uuid) or self.is_not_mine(meeting_uuid)

    def resolved_uuids(self) -> set[str]:
        """Every meeting already decided, in one query.

        A poll walks 600+ meetings, and asking per meeting opened two sqlite
        connections each — 1,200 against a database the refine sweeper is writing
        at the same time. Under the default journal mode a writer blocks readers,
        so the walk spent its time waiting on locks rather than on Zoom: the same
        poll took 3.5 minutes against an idle scratch file and was still going
        after 15 against the live one. One read up front, then pure set lookups.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT meeting_uuid FROM zoom_meetings UNION SELECT meeting_uuid FROM zoom_not_mine"
            ).fetchall()
        return {r["meeting_uuid"] for r in rows}

    def is_ingested(self, meeting_uuid: str) -> bool:
        with self._conn() as c:
            return (
                c.execute(
                    "SELECT 1 FROM zoom_meetings WHERE meeting_uuid=?", (meeting_uuid,)
                ).fetchone()
                is not None
            )


def ingest_assets(
    assets: dict[str, Any],
    todos: TodoStore,
    store: ZoomIngestStore,
    *,
    owners: list[str] | None = None,
    stage: str = "Captured",
    mode: str = DEFAULT_SOURCE_MODE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Turn one meeting's assets into captures, skipping anything already seen.

    `owners` optionally restricts capture to matching owner substrings
    (case-insensitive). Unowned items are always kept: an item with no named
    owner in Justin's own notes is his by default.
    """
    meeting_uuid = str(assets.get("meeting_uuid") or "").strip()
    if not meeting_uuid:
        raise ValueError("assets missing meeting_uuid")
    topic = str(assets.get("topic") or "").strip() or "Zoom meeting"
    start_time = str(assets.get("start_time") or "").strip()
    # Where to read the rest of the meeting from — the notes doc, then the
    # summary doc, then the meeting itself.
    links = [
        str((assets.get("my_notes") or {}).get("file_link") or ""),
        str((assets.get("meeting_summary") or {}).get("summary_doc_url") or ""),
        str(assets.get("deep_url") or ""),
    ]
    links = [x for x in links if x]

    items = extract_items(assets, mode=mode)
    if owners:
        # Yours, plus anything that names you. "Nicole: Share TowBook access with
        # Justin" is Nicole's task and Justin's dependency, and dropping it loses
        # the half of a 1:1 he has to chase. An item with no named owner is his by
        # default — it came out of his own notes.
        wanted = [o.lower().strip() for o in owners if o.strip()]
        items = [
            i
            for i in items
            if not i.owner
            or any(w in i.owner.lower() for w in wanted)
            or any(w in i.text.lower() for w in wanted)
        ]

    already = store.seen_keys(meeting_uuid)
    fresh = [i for i in items if i.key not in already]

    tags = ",".join(t for t in ("zoom", _slug(topic)) if t)
    created: list[dict[str, Any]] = []

    for item in fresh:
        record = {"todo_id": "", "text": item.as_todo_text(), "owner": item.owner, "origin": item.source}
        if not dry_run:
            # The verbatim capture keeps the meeting and a way back to it, so a
            # card still explains itself once the sweeper has redrafted the title.
            raw = f"{item.as_todo_text()} [{topic}"
            raw += f", {start_time[:10]}]" if start_time else "]"
            if links:
                raw += "\n" + "\n".join(links)
            todo = todos.add(
                item.as_todo_text(), tags=tags, stage=stage, source=SOURCE, raw=raw
            )
            store.record_item(meeting_uuid, item, todo.id)
            record["todo_id"] = todo.id
        created.append(record)

    if not dry_run:
        store.record_meeting(meeting_uuid, topic, start_time, len(fresh))

    return {
        "meeting_uuid": meeting_uuid,
        "topic": topic,
        "start_time": start_time,
        "extracted": len(items),
        "created": len(fresh),
        "skipped_duplicate": len(items) - len(fresh),
        "tags": tags,
        "dry_run": dry_run,
        "items": created,
    }
