# Brutus / Atlas5 pipeline unfuck

> **Historical scope:** For the current voice, session-continuity and release direction, use the [Apple accessibility reset plan](APPLE_ACCESSIBILITY_RESET_PLAN.md). This document remains evidence of its dated work; it does not override the reset requirements or prove current voice acceptance.

**SSOT (fowler-brain):** `~/fowler-brain/strategy/plans/operator/2026-08-03-brutus-pipeline-unfuck.md`  
**Date:** 2026-08-03  
**Status:** done (P0–P3 shipped)

## Goal

Finish the operator pipeline after answer-resume recovery: ship Brutus fix, clear stale awaiting flags, land Atlas4 on Studio, make the queue drain, harden flakes.

## Phases

| Phase | What | Done when |
|-------|------|-----------|
| **P0** | Commit Brutus recovery; clear `awaiting_input` on successful resume | Answer → resume clean; no stale Needs-you scar |
| **P1** | Deploy Atlas4 steering re-drop; harden `list_awaiting_input` retries | Studio clients resume without Brutus; board survives disconnects |
| **P2** | Diagnose 50 queued / 0 working; fix claim/drain | Backlog moves; alarm honest |
| **P3** | Secrets soft-load; capped-attempts ops; gate UX (no auto-approve) | Keys stick; uncapping is intentional; Justin still decides |

## Already fixed (do not re-diagnose)

- Answer save + resume when inbox WO missing → Brutus `answer_steering` re-drops + resumes (`client.py`).
- UI surfaces `dispatch_error` / recovery messaging (`ui.py`).
- Atlas4 `_redrop_and_dispatch` + inbox cap live on Studio (`0370c4b`).
- Ghost `started/running` after worker restart jammed WIP (REV-256): closed as delivered; worker now calls `reap_stale_ledger()` every cycle (`80edbce`).
- Soft-load: `~/.brutus/secrets.env` cache + parallel `op read` (`brutus/secrets_softload.py`). Restart uses cache-fresh in ~0s.
- Capped attempts: Work page section + `GET/POST /api/capped_attempts` (confirm required).
- Gate UX: Needs you splits **Answer** vs **Decide**; no auto-approve.

## Out of scope

UI density / C8, React rewrite, auto-approving Justin gates.

## Live findings (2026-08-03)

- Factory stall was **not** "worker dead" — Atlas5 was live on REV-256.
- Atlas6 deferred everything with `WIP full (7/1 inflight=0 inbox=5)` because worker `queue_depth=7` from **root + `sfdc/` duplicates** and stale webhook drops.
- **P2 alarm pass:** REV-256 ghost closed → `done=11`, completion_alarm off.
- **P3 soft-load:** err.log showed repeated `CURSOR_API_KEY: timeout>15s` / `NOT loaded`. Cache + 25s timeout + parallel refresh fixes it; verified cache-fresh on kickstart.

## Definition of done

| Criterion | Status |
|-----------|--------|
| Brutus recovery on branch Justin runs | **Done** |
| Answer → resume, no stale awaiting_input | **Done** |
| Atlas4 fix live on Studio | **Done** `0370c4b` / `80edbce` |
| Work claimed when backlog exists | **Done** |
| Decide gates still human | **Done** (Answer vs Decide split; no auto-approve) |
| Completion alarm clears | **Done** |
| Soft-load keys stick | **Done** (cache-fresh on restart) |
| Capped-attempts operator path | **Done** (`/api/capped_attempts` + UI) |

## Tracker

- [x] P0 Brutus commit/push
- [x] P0 clear approval_state on resume success
- [x] P1 Atlas4 on Studio
- [x] P1 list_awaiting retries
- [x] P2 queue drain
- [x] P2 alarm clears after real completions
- [x] P2b durable inbox cap
- [x] Re-queue REV-257 after 256 finishes
- [x] P2c ship ghost-reap-every-cycle to Studio (`80edbce`, Atlas5 pid 34323)
- [x] P3 secrets / caps / gates
