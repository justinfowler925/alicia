# Focus surface — build plan (ship as one)

> **Historical scope:** For the current voice, session-continuity and release direction, use the [Apple accessibility reset plan](APPLE_ACCESSIBILITY_RESET_PLAN.md). This document remains evidence of its dated work; it does not override the reset requirements or prove current voice acceptance.

**Date:** 2026-08-10  
**Audit:** shine adoption pass on live `~/.brutus/app` @ `f1f61d5`  
**Branch:** `feat/focus-surface`  
**Rule:** no waves. One PR. The product that already knows the truth (`/api/focus`) becomes the screen.

## Live proof that justified this

| Source | Headline / shape |
|---|---|
| `GET /api/focus` | *5 things need you* — one batchable gate of 13× `no_artifact` |
| `GET /api/board` | *17 things need you* — 15 peer Approve/Reject cards |
| UI | neither `ui.py` nor `session.js` called `/api/focus` |
| Factory | completion alarm 143h stale @ 6h window; 86 open / 1 in flight |
| Queue | 148 items under Session “Focus” in **REFINING** (filter ≠ focus API) |

Craft already PASSed shine measure. Failure is decidability.

## What ships

1. **Board API carries focus actions + focus headline** — one fetch, one “needs you” number.
2. **Work page renders `actions`**, not 17 peer decide rows. Batch Approve/Reject with blast-radius confirm.
3. **`no_artifact` humanized** in `human_reason` (and any sibling opaque blockers we hit).
4. **Answer cards** show ticket id + real title; question is the body.
5. **Session filter “Focus” → “Active”** (hide meeting dumps). Ops ↔ Session deep links.
6. **Refining unjam** — tight column page, bury old drafts, bulk “Ready” for drafts with no open `missing`.
7. **Morning brief on Work** — `GET /api/brief` strip at top of the ritual surface.
8. **Cross-stream strip** on the Work headline — drafts / agents / projects-at-risk / demos-down.
9. **Alarm fatigue** — banner once; chat dock does not re-paste the same alarm every poll.

## Non-goals

- CRO suite inside Brutus
- New Slack app (brief is on-screen + existing MCP/CLI; doorbell already speaks board deltas)
- Rewriting Atlas6 ledger semantics

## Done when

- Work headline matches `/api/focus` headline.
- One `no_artifact` batch → one card → one confirm naming N tickets.
- Session has no control labeled “Focus”.
- Refining does not open to 100+ peer cards.
- `/` and `/session` link to each other.
- Tests cover `no_artifact` labeling + board `actions` payload.
