# Session Ideas build plan

> **Historical scope:** For the current voice, session-continuity and release direction, use the [Apple accessibility reset plan](APPLE_ACCESSIBILITY_RESET_PLAN.md). This document remains evidence of its dated work; it does not override the reset requirements or prove current voice acceptance.

**Source audit:** Shine audit 2026-08-08 — canvas `brutus-session-ui-audit`  
**Target:** `/session` (`brutus/static/session.*`) + note tools / chat recipes  
**Branch:** `feat/session-ideas-panel`

## Problem

`/session` never names its three stores. The Ideas pad (63 notes) is invisible
there; Board reads as editable workstreams but is ledger focus-only; Captured
steals the “work capture” meaning. Update/delete for ideas have no spoken path.

## Ship checklist

| # | Item | Status |
|---|---|---|
| 1 | Ideas panel on `/session` with typed add / done / delete (confirm) | done |
| 2 | Spoken recipes: update (rename / move / done) + gated delete | done |
| 3 | Rename Board → Ledger; kill Status; demote Captured to `<details>` | done |
| 4 | Composer + empty teach grammar | done |
| 5 | SSE `idea` events on reserved bus id `ideas` + flash on land | done |
| 6 | Capture reply names the store (“On Ideas — …”) | done |
| 7 | Tests for recipes + HTML markers | done |

## Grammar (spoken = typed via `/say`)

| Intent | Phrase | Tool | Gate |
|---|---|---|---|
| Add idea | `capture …` / `note …` / `remember …` | `capture_note` | free |
| Add ledger | `track …` / `open a workstream for …` | `register_thread` | gated |
| Rename | `rename <id\|phrase> to …` | `update_note` | free |
| Move | `move <…> to inbox\|doing\|blocked\|done` | `update_note` | free |
| Done | `mark <…> done` / `done: …` | `update_note` | free |
| Delete | `delete note <…>` / `drop idea <…>` | `delete_note` | gated |
| List | `show notes` / `my notes` | `list_notes` | read |
| Focus ledger | `REV-N` / `back` / `everything` | (client) | — |

## Layout

```
Conversation | Ideas + Ledger | Thinking + Proposed + Captured(details)
```

## Non-goals

- Rewriting `ui.py` `/` Notes kanban (stays until Ideas is verified live)
- Full DataGrid chrome on Ideas (search/sort later if the list stays large)
- Changing Atlas ledger mutation paths beyond existing Proposed gate

## Verify

- `pytest` recipe + session HTML marker tests
- Live: add/rename/done/delete by type and by Talk; ledger focus still works
- Capture flash lands in Ideas; delete requires Proposed confirm
