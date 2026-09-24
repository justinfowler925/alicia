# Voice follow-through eval

> **Historical scope:** For the current voice, session-continuity and release direction, use the [Apple accessibility reset plan](APPLE_ACCESSIBILITY_RESET_PLAN.md). This document remains evidence of its dated work; it does not override the reset requirements or prove current voice acceptance.

Date: 2026-08-25

## Scenario

The eval seeds the exact failed exchange: Alicia offers to pull up REV-507, then
Justin says, `Go ahead. I'm listening.` It drives the real
`ConversationManager` with Claude and a deterministic `get_thread` fixture.
Every run must call that tool exactly once, return the decision, avoid repeating
the prior summary or asking the same permission again, and end on a complete
sentence. One failure fails the run.

Runner: `scripts/eval-voice-follow-through.py`

## Pre-change production artifact

Artifact: `db750829007381068132b85e232d115b9cc6002b`

- Result: **0/5 passed**
- Mean turn latency: **3.407 seconds**
- All five runs called `get_thread`, then appended the same robotic permission
  loop: `Want me to queue the approval?`

## Post-change candidate

- Result: **15/15 passed**
- Mean turn latency: **4.906 seconds**
- All 15 runs called `get_thread` exactly once and returned REV-507's decision.
- No run repeated the prior summary, re-offered the lookup, leaked a second
  `want me to` permission loop, or ended mid-sentence.
- Full automated suite: **661/661 passed**.

These are model-backed scratch-state evals, not synthetic turns written into
the live Alicia session store. Production verification still requires the
merged artifact identity and a live voice turn after deploy.
