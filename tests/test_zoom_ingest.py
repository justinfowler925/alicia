"""Zoom AI Companion ingest using synthetic repository-safe fixtures."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import fields
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from alicia.config import AliciaCfg
from alicia.server import create_app
from alicia.todos import Todo, TodoStore
from alicia.zoom_ingest import (
    ZoomIngestStore,
    extract_items,
    ingest_assets,
    parse_action_items,
    parse_next_steps,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def itc() -> dict:
    return _load("zoom_assets_product_sync.json")


@pytest.fixture
def empty() -> dict:
    return _load("zoom_assets_no_items.json")


@pytest.fixture
def stores(tmp_path: Path) -> tuple[TodoStore, ZoomIngestStore]:
    """Own sqlite file — never the shared state dir the daemon serves."""
    db = tmp_path / "todos.sqlite"
    return TodoStore(db), ZoomIngestStore(db)


# --- parsing ---------------------------------------------------------------


def test_action_items_parsed_with_owners(itc: dict) -> None:
    items = parse_action_items(itc["my_notes"]["content_markdown"])
    assert len(items) == 6
    owners = [i.owner for i in items]
    assert "Alex" in owners
    assert "Bailey + Casey + Drew" in owners
    alex = next(i for i in items if i.owner == "Alex")
    assert "interface" in alex.text
    assert all(i.source == "action_items" for i in items)


def test_next_steps_owners_come_from_h3(itc: dict) -> None:
    items = parse_next_steps(itc["meeting_summary"]["summary_markdown"])
    owners = {i.owner for i in items}
    assert "Alex Example" in owners
    assert "Bailey Example" in owners
    # "Collaboration" is a bucket heading, not a person.
    assert "Collaboration" not in owners
    assert all(i.source == "next_steps" for i in items)


def test_next_steps_strips_zoom_task_links(itc: dict) -> None:
    items = parse_next_steps(itc["meeting_summary"]["summary_markdown"])
    assert items, "fixture should contain next steps"
    for i in items:
        assert "tasks.zoom.us" not in i.text
        assert "http" not in i.text
        assert "](" not in i.text


def test_placeholder_prose_never_becomes_a_task(empty: dict) -> None:
    """'No action items assigned.' is prose in a bullet list, not a commitment."""
    assert parse_action_items(empty["my_notes"]["content_markdown"]) == []
    assert parse_next_steps(empty["meeting_summary"]["summary_markdown"]) == []
    assert extract_items(empty) == []


def test_default_mode_uses_my_notes_alone(itc: dict) -> None:
    """The two sections restate each other; blending them duplicates the meeting."""
    items = extract_items(itc)
    assert {i.source for i in items} == {"action_items"}
    assert len(items) == 6


def test_both_mode_reads_both_sections(itc: dict) -> None:
    items = extract_items(itc, mode="both")
    assert {i.source for i in items} == {"action_items", "next_steps"}
    assert len(items) > 6


def test_default_mode_drops_the_reworded_duplicates(itc: dict) -> None:
    """The concrete overlap that made ranking the sections the right call.

    My Notes and the summary both ask Alex to share the interface checklist.
    The default takes the curated section whole instead of attempting fuzzy
    matching across two independently worded sources.
    """
    checklist = [i for i in extract_items(itc) if "interface review" in i.text.lower()]
    assert len(checklist) == 1
    assert checklist[0].source == "action_items"
    # In "both" mode the duplicate is present and accepted, by choice.
    assert len(
        [i for i in extract_items(itc, mode="both") if "interface review" in i.text.lower()]
    ) == 2


def test_falls_back_to_next_steps_when_notes_have_none(itc: dict) -> None:
    """Meetings where nobody took notes still have a summary."""
    no_notes = json.loads(json.dumps(itc))
    no_notes["my_notes"]["content_markdown"] = "## Key Outcomes\n\nprose only\n"
    items = extract_items(no_notes)
    assert items
    assert {i.source for i in items} == {"next_steps"}


def test_unknown_mode_rejected(itc: dict) -> None:
    with pytest.raises(ValueError, match="mode"):
        extract_items(itc, mode="everything")


def test_extract_handles_missing_sections() -> None:
    assert extract_items({}) == []
    assert extract_items({"my_notes": {}, "meeting_summary": {}}) == []
    assert extract_items({"my_notes": {"content_markdown": "## Key Outcomes\n\nprose only"}}) == []


def test_decisions_and_open_questions_are_not_captured(itc: dict) -> None:
    """Only Action Items and Next steps are commitments.

    "Decisions Made" and "Open Questions" sit in the same document and read like
    bullets; capturing them would put settled decisions in the Inbox as work.
    """
    texts = {i.text.lower() for i in extract_items(itc)}
    assert not any("customer and employee data" in t for t in texts)
    assert not any(t.startswith("how should the public example") for t in texts)


# --- ingest ----------------------------------------------------------------


def test_ingest_creates_captures_in_the_inbox(itc: dict, stores) -> None:
    todos, store = stores
    result = ingest_assets(itc, todos, store)
    assert result["created"] == result["extracted"] > 0
    rows = todos.list()
    assert len(rows) == result["created"]
    assert all(t.stage == "Captured" for t in rows)
    assert all(t.source == "zoom" for t in rows)
    assert all("zoom" in t.tags.split(",") for t in rows)
    assert all("product-sync" in t.tags for t in rows)
    # The verbatim capture keeps the meeting, so a redrafted title still explains itself.
    assert all("[Product sync" in t.raw for t in rows)


def test_ingest_is_idempotent(itc: dict, stores) -> None:
    """The whole point: an hourly job must not refill the pad every hour."""
    todos, store = stores
    first = ingest_assets(itc, todos, store)
    second = ingest_assets(itc, todos, store)
    assert second["created"] == 0
    assert second["skipped_duplicate"] == first["extracted"]
    assert len(todos.list()) == first["created"]


def test_reingest_picks_up_only_new_items(itc: dict, stores) -> None:
    """Notes edited after the meeting add the delta, not the whole set again."""
    todos, store = stores
    first = ingest_assets(itc, todos, store)
    grown = json.loads(json.dumps(itc))
    grown["my_notes"]["content_markdown"] = grown["my_notes"][
        "content_markdown"
    ].replace(
        "\n## Decisions Made",
        "\n- **Alex**: Book the review room.\n\n## Decisions Made",
    )
    second = ingest_assets(grown, todos, store)
    assert second["created"] == 1
    assert "review room" in second["items"][0]["text"].lower()
    assert len(todos.list()) == first["created"] + 1


def test_dedupe_survives_whitespace_and_case_drift(itc: dict, stores) -> None:
    """Zoom re-emits the same item with case and spacing drift."""
    todos, store = stores
    ingest_assets(itc, todos, store)
    reworded = json.loads(json.dumps(itc))
    reworded["my_notes"]["content_markdown"] = reworded["my_notes"]["content_markdown"].replace(
        "- **Bailey**: Serve demo publicly", "- **bailey**:  Serve   demo publicly"
    )
    assert ingest_assets(reworded, todos, store)["created"] == 0


def test_capture_keeps_a_way_back_to_the_meeting(itc: dict, stores) -> None:
    """A redrafted title must still be traceable to the meeting that produced it."""
    todos, store = stores
    ingest_assets(itc, todos, store)
    raw = todos.list()[0].raw
    assert "[Product sync, 2026-01-15]" in raw
    assert "docs.zoom.us" in raw


def test_dry_run_writes_nothing(itc: dict, stores) -> None:
    todos, store = stores
    result = ingest_assets(itc, todos, store, dry_run=True)
    assert result["created"] > 0
    assert todos.list() == []
    assert store.meetings() == []
    # A dry run must not burn the dedupe keys either.
    assert ingest_assets(itc, todos, store)["created"] == result["created"]


def test_owner_filter_keeps_unowned_items(itc: dict, stores) -> None:
    todos, store = stores
    result = ingest_assets(itc, todos, store, owners=["alex"])
    assert result["created"] > 0
    for item in result["items"]:
        assert item["owner"] == "" or "alex" in item["owner"].lower()
    # And it really did drop somebody else's work.
    assert result["created"] < len(extract_items(itc))


def test_endpoint_honours_source_mode(api, itc: dict) -> None:
    body = api.post("/api/zoom/ingest", json={**itc, "mode": "both"}).json()
    assert body["created"] > 6


def test_empty_meeting_records_no_captures(empty: dict, stores) -> None:
    todos, store = stores
    result = ingest_assets(empty, todos, store)
    assert result["created"] == 0
    assert todos.list() == []
    # Still marked ingested, so it is not re-examined forever.
    assert store.is_ingested(empty["meeting_uuid"])


def test_missing_uuid_rejected(stores) -> None:
    todos, store = stores
    with pytest.raises(ValueError, match="meeting_uuid"):
        ingest_assets({"topic": "x"}, todos, store)


def test_todo_text_truncated_to_column_limit(stores) -> None:
    todos, store = stores
    assets = {
        "meeting_uuid": "LONG-1",
        "topic": "Long",
        "my_notes": {"content_markdown": "## Action Items\n\n- **Justin**: " + "x" * 900 + "\n"},
    }
    assert ingest_assets(assets, todos, store)["created"] == 1
    assert len(todos.list()[0].text) <= 500


def test_status_reports_ingested_meetings(itc: dict, empty: dict, stores) -> None:
    todos, store = stores
    ingest_assets(itc, todos, store)
    ingest_assets(empty, todos, store)
    meetings = store.meetings()
    assert {m["meeting_uuid"] for m in meetings} == {itc["meeting_uuid"], empty["meeting_uuid"]}
    itc_row = next(m for m in meetings if m["meeting_uuid"] == itc["meeting_uuid"])
    assert itc_row["item_count"] > 0
    assert itc_row["topic"] == "Product sync"


# --- the invariant that protects the deployed daemon -----------------------


def test_ingest_never_reshapes_the_todos_table(itc: dict, stores) -> None:
    """Ingest bookkeeping must not add a column to the shared `todos` table.

    A branch that added seven columns to ~/.alicia/state on 2026-08-08 broke
    every read path on deployed main. New *tables* are safe — no older reader
    selects from them — so the ledger lives in its own two.
    """
    todos, store = stores
    expected = {f.name for f in fields(Todo)}
    ingest_assets(itc, todos, store)
    with sqlite3.connect(str(todos.path)) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(todos)")}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert cols == expected
    assert {"zoom_meetings", "zoom_items"} <= tables
    # And the deployed row mapper still reads the rows it just wrote.
    assert todos.list()


# --- the HTTP lane ---------------------------------------------------------


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The real app on a per-test state dir.

    The conftest isolation is session-scoped, so without this every ingest test
    would inherit the previous one's rows and the idempotency assertions would
    pass for the wrong reason.
    """
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path))
    cfg = AliciaCfg(atlas6_url="http://127.0.0.1:8767", watchdog_enabled=False)
    with patch("alicia.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        with TestClient(create_app(cfg, start_watchdog=False)) as client:
            yield client


def test_endpoint_ingests_and_shows_up_in_the_pad(api, itc: dict) -> None:
    r = api.post("/api/zoom/ingest", json=itc)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["created"] > 0
    assert body["errors"] == []

    todos = api.get("/api/todos").json()["todos"]
    zoom_rows = [t for t in todos if t["source"] == "zoom"]
    assert len(zoom_rows) == body["created"]
    assert all(t["stage"] == "Captured" for t in zoom_rows)
    assert all("zoom" in t["tags"].split(",") for t in zoom_rows)


def test_endpoint_is_idempotent(api, itc: dict) -> None:
    first = api.post("/api/zoom/ingest", json=itc).json()
    second = api.post("/api/zoom/ingest", json=itc).json()
    assert second["created"] == 0
    assert second["skipped_duplicate"] == first["created"]
    zoom_rows = [t for t in api.get("/api/todos").json()["todos"] if t["source"] == "zoom"]
    assert len(zoom_rows) == first["created"]


def test_endpoint_batch_and_skip_ingested(api, itc: dict, empty: dict) -> None:
    body = api.post("/api/zoom/ingest", json={"meetings": [itc, empty]}).json()
    assert body["meetings_processed"] == 2
    assert body["created"] > 0
    again = api.post(
        "/api/zoom/ingest", json={"meetings": [itc, empty], "skip_ingested": True}
    ).json()
    assert all(r.get("skipped_meeting") for r in again["results"])
    assert again["created"] == 0


def test_endpoint_dry_run_creates_nothing(api, itc: dict) -> None:
    body = api.post("/api/zoom/ingest", json={**itc, "dry_run": True}).json()
    assert body["dry_run"] is True
    assert body["created"] > 0
    assert [t for t in api.get("/api/todos").json()["todos"] if t["source"] == "zoom"] == []


def test_endpoint_reports_bad_payload_without_500(api) -> None:
    body = api.post("/api/zoom/ingest", json={"meetings": [{"topic": "no uuid"}]}).json()
    assert body["ok"] is False
    assert "meeting_uuid" in body["errors"][0]["error"]


def test_endpoint_one_bad_meeting_does_not_sink_the_batch(api, itc: dict) -> None:
    body = api.post("/api/zoom/ingest", json={"meetings": [{"topic": "bad"}, itc]}).json()
    assert body["created"] > 0
    assert len(body["errors"]) == 1


def test_status_endpoint_lists_ingested_meetings(api, itc: dict) -> None:
    api.post("/api/zoom/ingest", json=itc)
    meetings = api.get("/api/zoom/ingest").json()["meetings"]
    assert any(m["meeting_uuid"] == itc["meeting_uuid"] for m in meetings)


# --- the autonomous poll lane ---------------------------------------------


class _FakeZoom:
    """Stands in for ZoomClient. Records which meetings were resolved."""

    LISTED: ClassVar[list[dict]] = [
        {  # hosted by Justin, has content
            "meeting_uuid": "own==",
            "meeting_topic": "Data Project Update",
            "meeting_host_email": "justin.fowler@clearspeed.com",
            "meeting_start_time": "2026-08-11T14:00:00Z",
        },
        {  # hosted by someone else, Justin attended
            "meeting_uuid": "attended==",
            "meeting_topic": "ITC sync",
            "meeting_host_email": "maria.pocovi@clearspeed.com",
            "meeting_start_time": "2026-08-11T16:15:00Z",
        },
        {  # nothing to do with Justin
            "meeting_uuid": "theirs==",
            "meeting_topic": "Patrick / Maria",
            "meeting_host_email": "maria.pocovi@clearspeed.com",
            "meeting_start_time": "2026-08-11T19:30:00Z",
        },
    ]
    SUMMARIES: ClassVar[dict[str, dict]] = {
        "own==": {"next_steps": ["Justin: Write the ingest runbook."]},
        "attended==": {"next_steps": ["Jimmy Gibson: Serve the demo publicly."]},
        "theirs==": {"next_steps": ["Maria: Something private."]},
    }
    PARTICIPANTS: ClassVar[dict[str, set]] = {
        "attended==": {"justin.fowler@clearspeed.com", "maria.pocovi@clearspeed.com"},
        "theirs==": {"maria.pocovi@clearspeed.com", "patrick.smyth@clearspeed.com"},
    }

    def __init__(self, *a, **k) -> None:
        self.summaries_fetched: list[str] = []
        self.participants_checked: list[str] = []

    def list_summaries(self, frm, to, **k):
        return list(self.LISTED)

    def get_summary(self, uuid):
        self.summaries_fetched.append(uuid)
        return self.SUMMARIES.get(uuid, {})

    def participant_emails(self, uuid):
        self.participants_checked.append(uuid)
        return self.PARTICIPANTS.get(uuid, set())


@pytest.fixture
def poll_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path))
    fake = _FakeZoom()
    monkeypatch.setattr("alicia.server.ZoomClient", lambda *a, **k: fake)
    cfg = AliciaCfg(atlas6_url="http://127.0.0.1:8767", watchdog_enabled=False)
    with patch("alicia.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        with TestClient(create_app(cfg, start_watchdog=False)) as client:
            yield client, fake


def test_poll_takes_hosted_and_attended_but_not_other_peoples(poll_api) -> None:
    client, fake = poll_api
    body = client.post("/api/zoom/poll", json={"days": 7}).json()
    assert body["ok"] is True
    assert body["summaries_listed"] == 3
    assert body["mine"] == 2, "hosted + attended, never the meeting he was not in"
    assert "theirs==" not in fake.summaries_fetched
    texts = [t["text"] for t in client.get("/api/todos").json()["todos"]]
    assert any("ingest runbook" in t for t in texts)
    assert not any("Something private" in t for t in texts)


def test_poll_is_idempotent_across_runs(poll_api) -> None:
    """An hourly job must stop re-fetching what it already has."""
    client, fake = poll_api
    first = client.post("/api/zoom/poll", json={"days": 7}).json()
    assert first["created"] > 0
    fake.summaries_fetched.clear()
    second = client.post("/api/zoom/poll", json={"days": 7}).json()
    assert second["created"] == 0
    assert second["mine"] == 0
    assert fake.summaries_fetched == [], "an ingested meeting is not re-fetched at all"


def test_poll_owner_filter_keeps_items_naming_him(poll_api) -> None:
    client, _ = poll_api
    body = client.post("/api/zoom/poll", json={"days": 7, "owners": ["justin"]}).json()
    texts = [t["text"] for t in client.get("/api/todos").json()["todos"]]
    assert body["created"] == 1
    assert any("ingest runbook" in t for t in texts)
    assert not any("Serve the demo publicly" in t for t in texts)


def test_poll_dry_run_writes_nothing(poll_api) -> None:
    client, _ = poll_api
    body = client.post("/api/zoom/poll", json={"days": 7, "dry_run": True}).json()
    assert body["created"] > 0
    assert client.get("/api/todos").json()["todos"] == []


def test_poll_reports_credential_failure_as_503(tmp_path: Path, monkeypatch) -> None:
    """launchd should see a retryable status, not a stack trace."""
    from alicia.zoom_api import ZoomAPIError

    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path))

    def _boom(*a, **k):
        raise ZoomAPIError("missing Zoom credentials: ZOOM_CLIENT_SECRET")

    monkeypatch.setattr("alicia.server.ZoomClient", _boom)
    cfg = AliciaCfg(atlas6_url="http://127.0.0.1:8767", watchdog_enabled=False)
    with patch("alicia.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        with TestClient(create_app(cfg, start_watchdog=False)) as client:
            r = client.post("/api/zoom/poll", json={})
    assert r.status_code == 503
    assert "credentials" in r.json()["detail"]


def test_not_mine_verdict_is_remembered(poll_api) -> None:
    """The expensive half of a poll is deciding somebody else's meeting is theirs.

    Attendance cannot change retroactively, so the participants call is paid once
    per meeting. Without this an hourly job re-checks every company meeting 24
    times a day, which is what made a 30-day poll take minutes.
    """
    client, fake = poll_api
    first = client.post("/api/zoom/poll", json={"days": 7}).json()
    assert "theirs==" in fake.participants_checked
    assert first["already_resolved"] == 0

    fake.participants_checked.clear()
    second = client.post("/api/zoom/poll", json={"days": 7}).json()
    assert fake.participants_checked == [], "no meeting should be re-resolved"
    assert second["already_resolved"] == 3
    assert second["created"] == 0


def test_dry_run_does_not_poison_the_not_mine_cache(poll_api) -> None:
    """A dry run must leave the next real run exactly as much work to do."""
    client, fake = poll_api
    client.post("/api/zoom/poll", json={"days": 7, "dry_run": True})
    fake.participants_checked.clear()
    body = client.post("/api/zoom/poll", json={"days": 7}).json()
    assert body["already_resolved"] == 0
    assert body["created"] > 0


def test_resolved_uuids_covers_both_ledgers(itc: dict, empty: dict, stores) -> None:
    """One query must return everything already decided, ingested or not-mine."""
    todos, store = stores
    ingest_assets(itc, todos, store)
    store.mark_not_mine("theirs==", "Patrick / Maria")
    resolved = store.resolved_uuids()
    assert itc["meeting_uuid"] in resolved
    assert "theirs==" in resolved
    assert empty["meeting_uuid"] not in resolved
    # And it agrees with the per-meeting predicate it replaced.
    for uuid in (itc["meeting_uuid"], "theirs==", empty["meeting_uuid"]):
        assert (uuid in resolved) is store.is_resolved(uuid)


def test_poll_reads_the_ledger_once_not_per_meeting(poll_api, monkeypatch) -> None:
    """The walk is 600+ meetings; a per-meeting query made it lock-bound."""
    client, _ = poll_api
    import alicia.zoom_ingest as zi

    calls = {"n": 0}
    original = zi.ZoomIngestStore.resolved_uuids

    def counted(self):
        calls["n"] += 1
        return original(self)

    monkeypatch.setattr(zi.ZoomIngestStore, "resolved_uuids", counted)
    client.post("/api/zoom/poll", json={"days": 7})
    assert calls["n"] == 1


def test_poll_writes_not_mine_verdicts_in_one_batch(poll_api, monkeypatch) -> None:
    """Hundreds of separate writes on the daemon's own database is the slow path."""
    client, _ = poll_api
    import alicia.zoom_ingest as zi

    batches: list[int] = []
    original = zi.ZoomIngestStore.mark_many_not_mine

    def counted(self, pairs):
        batches.append(len(pairs))
        return original(self, pairs)

    monkeypatch.setattr(zi.ZoomIngestStore, "mark_many_not_mine", counted)
    body = client.post("/api/zoom/poll", json={"days": 7}).json()
    assert body["marked_not_mine"] == 1
    assert batches == [1], "one commit for the whole walk"
