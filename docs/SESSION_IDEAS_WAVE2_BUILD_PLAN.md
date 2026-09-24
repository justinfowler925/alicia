# Session Ideas — wave 2 build plan

> **Historical scope:** For the current voice, session-continuity and release direction, use the [Apple accessibility reset plan](APPLE_ACCESSIBILITY_RESET_PLAN.md). This document remains evidence of its dated work; it does not override the reset requirements or prove current voice acceptance.

**Depends on:** PR #28 (`93a82e8`) — Ideas pad + spoken CRUD + Ledger rename  
**Source audit:** Shine audit 2026-08-08 remaining gaps  
**Branch:** `feat/session-ideas-wave2`  
**Target:** `/session` (`alicia/static/session.*`)

## Problem

Wave 1 made the Ideas pad visible and renamed Board → Ledger. The audit still
called out: Ideas missing search/edit/promote (~63 notes), ledger click not
opening thread detail, and board load failure looking like an empty ledger.

## Ship checklist

| # | Item | Status |
|---|---|---|
| 1 | Ideas search/filter input | done |
| 2 | Inline edit (Edit → PATCH `/api/todos/:id`) | done |
| 3 | Promote button (POST `/api/todos/:id/promote`) + status result | done |
| 4 | Doing lane control (PATCH lane `In Progress`) | done |
| 5 | Ledger click + spoken REV-N open thread detail (read-only) | done |
| 6 | Board load failure ≠ empty (“Couldn't reach the ledger…”) | done |
| 7 | Tests for HTML markers + wave2 JS symbols | done |

## Detail panel contract

Opened by focus (click or spoken ticket id). Fields from `/api/board` rows:

- ticket, title, question / reason / signal, age, lane, Linear link when present

**No silent ledger mutations** from this panel — writes stay on Proposed.

## Non-goals

- Full DataGrid (sort/page) on Ideas
- Ledger mutations from the detail panel
- Rewriting `ui.py` `/` Notes kanban

## Verify

- `pytest` session HTML marker + recipe tests
- Live: search/edit/promote/Doing; click and say `REV-N` open detail; kill Atlas → error empty state
