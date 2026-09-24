"""Tests for Alicia persistent memory."""

from pathlib import Path

from alicia.memory import Conversation, MemoryStore, WorkingNote


def test_save_and_list_conversations(tmp_path: Path) -> None:
    store = MemoryStore(path=tmp_path / "memory.sqlite")
    c = store.save_conversation(
        "scope the renewal tracker",
        "let's start with a 1x1 conversation table",
        title="Renewal tracker",
        summary="Scoping a tracker for renewal opps",
        linked_tickets=["REV-341"],
    )
    assert c.id
    assert c.title == "Renewal tracker"
    assert c.linked_tickets == ["REV-341"]

    got = store.get_conversation(c.id)
    assert got is not None
    assert got.last_user_message == "scope the renewal tracker"

    conversations = store.list_conversations()
    assert len(conversations) == 1
    assert conversations[0].id == c.id


def test_update_conversation(tmp_path: Path) -> None:
    store = MemoryStore(path=tmp_path / "memory.sqlite")
    c = store.save_conversation("hello", "hi")
    c2 = store.save_conversation(
        "what about REV-332?",
        "REV-332 is stuck on health score seeding",
        conversation_id=c.id,
        summary="Discussing REV-332 health score",
    )
    assert c2.id == c.id
    assert c2.summary == "Discussing REV-332 health score"
    assert len(store.list_conversations()) == 1


def test_working_notes(tmp_path: Path) -> None:
    store = MemoryStore(path=tmp_path / "memory.sqlite")
    n = store.add_working_note(
        "Paula wants title ladder review",
        "Schedule 30m with Paula and Jeffers to review title ladder content.",
        ticket_ids=["REV-305"],
    )
    assert n.id
    assert n.topic == "Paula wants title ladder review"
    assert n.ticket_ids == ["REV-305"]

    notes = store.list_working_notes()
    assert len(notes) == 1


def test_default_history_from_last_conversation(tmp_path: Path) -> None:
    store = MemoryStore(path=tmp_path / "memory.sqlite")
    assert store.default_history() == []
    store.save_conversation("design a tracker", "start with a 1x1 table")
    assert store.default_history() == [
        {"role": "user", "content": "design a tracker"},
        {"role": "assistant", "content": "start with a 1x1 table"},
    ]


def test_agent_overlay_upsert(tmp_path: Path) -> None:
    store = MemoryStore(path=tmp_path / "memory.sqlite")
    o = store.upsert_agent_overlay("cursor:abc", pinned=True, labels="alicia")
    assert o["pinned"] is True
    assert o["labels"] == "alicia"
    o2 = store.upsert_agent_overlay("cursor:abc", archived=True)
    assert o2["pinned"] is True
    assert o2["archived"] is True
    all_o = store.list_agent_overlays()
    assert "cursor:abc" in all_o
