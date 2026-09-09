# Conversation Rebuild Plan — one brain, gated hands

> **Historical scope:** For the current voice, session-continuity and release direction, use the [Apple accessibility reset plan](APPLE_ACCESSIBILITY_RESET_PLAN.md). This document remains evidence of its dated work; it does not override the reset requirements or prove current voice acceptance.

Date: 2026-08-19 · Status: **shipped** — Phases 1–3 landed in #80 (with #79's Sonnet/Cursor backend), Phase 4 STT config included; deployed 33587fb · Supersedes nothing (this is the first plan the conversation path has ever had; all seven prior docs in this directory are UI or factory plumbing).

## Problem

Diagnosed 2026-08-19 from the live session store (`~/.brutus/state/sessions.sqlite`, 395 sessions, 545 replies) and the deployed code. The architecture inverts the division of labor: deterministic code makes every judgment call and the models only phrase results.

- Routing is substring regex (`chat_resolve.py:147-384`) applied before any model reads the message. "What's the status of the Clearspeed pilot" → board template; the question is never read.
- The deep lane sends Claude Sonnet 5 **one message with zero history** (`conversation.py:810` → `claude.py:63`) under a generic 3-line system prompt. Follow-ups are structurally impossible.
- All three conversational Qwen calls run **thinking ON** — the configuration `local_llm.py:160-166` itself measured as 13.1s / zero content tokens on the production prompt shape. The health probe runs thinking OFF, so the zombie looks healthy.
- ~20 post-filters can discard whatever survives: 320-char/3-sentence cap on every fast reply, Claude answers flattened and cut to 8 sentences (~96% of paid output thrown away), `guard_invented_tickets` replacing entire answers with a board question.
- Memory has never stored a real fact: 54 of 55 `lessons` rows are the literal string "x"; no reply path reads the memory store.
- Measured outcomes: 60% of all replies are `capture_note` templates; 37 of 89 voice questions took >15s to a substantive answer (15 took 40–120s); 16 of 102 voice turns were Brutus transcribing its own TTS; 8 human turns since Aug 13 — usage has collapsed to machine `capture:` traffic.

Twenty-two commits (25% of repo history) re-fixed this path with the same four moves — add a regex, add a post-filter, add a prompt line, swap the model — and each one removed a phrasing from the model's reach while creating a new cliff. HEAD (`614e9e3`) already deletes its own interview machinery as "theatre". This plan finishes that direction deliberately instead of by attrition.

## Target architecture

**One conversational brain — Claude Sonnet 5 through the official `anthropic` SDK — with the full session history, a native tool catalog, and the write-gate system unchanged.**

- `claude.py` moves from raw httpx to the `anthropic` SDK. Native tool use (`tools=[...]`, `tool_use`/`tool_result` loop), not `TOOL:`/`ARGS:` text parsing.
- Every capability becomes a tool the brain calls: `get_work_surface`, `get_digest`, `get_thread`, `list_notes`, `check_email`, `check_slack`, `capture_note`, `draft_gate_action` (approve/dispatch/promote — returns "draft created, awaiting your yes"), `ask_atlas6`, `remember` / `recall`.
- `spoken_next_decision` survives **as a tool result**, never as a reply that preempts the model. The board template is a great input and a terrible mouth.
- The gate design stays exactly as is: writes go through draft artifacts and single-use approval (`gate.py`, `session.py:341-357`). That part of Brutus is sound. `read_only` remains a registry property the model cannot talk its way past.
- Deterministic handling survives only where determinism is the right tool: the `capture:` machine-intake prefix (agents' carry-forwards never touch a model — 337 of 471 lifetime user turns stay $0), UI barge-in/stop hotwords, and the own-voice echo filter.
- Deleted: `_lookup_intent` as a router, greeting/scrap/frustration canned replies, the frustration **message rewrite**, `_tighten` caps (TTS length lives in `speechify`, prompt asks for spoken-beat answers), `guard_invented_tickets` full-reply substitution (breach → one retry round with the board attached; still-breaching → annotate, don't replace).
- History: the full session's turns each call (`history_for_model` without `keep=4`), **no ticket-id redaction**. Long sessions get server-side compaction later if ever needed; at observed turn counts (median session <10 turns) it won't be.
- Qwen3-8B is **retired**, not demoted. It is not a fallback. Cursor (composer via `cursor_runner`) is the only alternate to Sonnet 5. Voice still refuses launching the Cursor agent (shell + repo).

## Phases

### Phase 0 — stop the bleeding (shipped in spirit by the 8B-kill PR)
The 8B is gone from conversation, refine, and summaries. Deep consult is Sonnet with Cursor as the explicit/failure alternate. Session history is packed into the consult payload. Do not re-enable `local_llm`.

### Phase 1 — the brain loop — DONE (#80, brain.brain_reply)
- SDK client, `messages.create` with `tools`, parallel `tool_result` blocks returned in a single user message, loop until `end_turn`.
- Prompt caching: stable prefix = system prompt + tool catalog with `cache_control: {"type": "ephemeral"}` on the last system block; volatile content (history, board) after it. Verify with `usage.cache_read_input_tokens` non-zero on turn 2 — zero means a silent invalidator.
- `output_config: {"effort": "low"}` per chat turn; raise to `high` when the brain escalates a hard question.
- `ConversationManager.handle` becomes: hotwords → `capture:` intake → brain loop → gate execution on approval. Nothing else.

DoD: the bake-off harness rebuilt to drive the production entry point (`handle`/`resolve_chat_reply`), not the bare model — the 2026-08-12 lesson. Scenarios: "create a todo for X" writes a draft note; "is it done" resolves the referent; "what needs me" calls the surface tool and speaks one decision; "approve REV-N" round-trips the gate; a bare "go" with a pending draft executes it and without one asks what to run.

### Phase 2 — delete the router — DONE (#80; capture:/rewind/gate stay deterministic)
- Remove `_lookup_intent` routing, `_is_greeting`/`_is_incomplete_scrap` canned replies, `is_frustration` rewrite (frustration becomes context the brain sees, plus immediate TTS cut in the UI).
- Kill filler acks. One honest UI "thinking" state; speak only when the answer exists. If p50 latency exceeds ~4s after Phase 1, a single short ack is allowed — measured first.

### Phase 3 — memory that works — DONE (#80 recall tool + standing notes; junk purged 2026-08-19, backup in state/backups)
- Back up, then purge the junk (54 "x" lessons, 56 junk working notes).
- `remember`/`recall` tools + top-K relevant memories injected into the cached prefix per session.

DoD: "put that in memory: it's rev, not revey" produces a row; a fresh session recalls and honors it.

### Phase 4 — voice input quality — config shipped (small.en, pre-cached); live-utterance check still open
- Ear STT: Whisper `base`/CPU → `large-v3-turbo` (or `small.en`) on MLX; the fixed 5s `record_duration_s` window is already irrelevant on push-to-talk.
- Browser open-mic keeps the 1s utterance buffer and content-based echo filter; both carry regression tests against the Aug-6 echo transcript.

## Cost: Sonnet 5 as the brain

Prices (Claude API, 2026-08): Sonnet 5 **$3/M input · $15/M output** (intro $2/$10 through 2026-08-31). Cache reads ~0.1× input; cache writes 1.25× (5-min TTL). Haiku 4.5 $1/$5. Opus 5 $5/$25.

Per-turn shape after Phase 1 (estimates; verify with `count_tokens` in the Phase 1 PR):

| Component | Tokens | Cost @ $3/$15 |
|---|---|---|
| Stable prefix (system + ~20 tool schemas), cached read × 2 rounds | ~8K read | $0.0024 |
| Fresh input (history delta + board/tool results + question) | ~4K | $0.012 |
| Output across rounds (tool calls + spoken answer) | ~600 | $0.009 |
| **Typical warm turn (2 rounds)** | | **≈ $0.024** |

Cold first turn of a session adds one ~4K cache write ≈ $0.015. Fully uncached worst case ≈ $0.05/turn.

| Usage level | Turns/day | Monthly |
|---|---|---|
| Measured (last 2 weeks of human traffic) | ~10 | **~$7/mo** |
| Heavy daily-driver | 50 | ~$35/mo |
| Captures / agent intake | 337 lifetime | $0 — never touch a model |

For calibration: the **current broken** deep lane already spends ~$0.02–0.04 per deep turn on uncached Sonnet 5 calls whose output is 96% discarded by the 8-sentence cap. The rebuild does not meaningfully change what Brutus costs — it changes what the money buys.

## More efficient paths, ranked

1. **Prompt caching** (in Phase 1) — the biggest lever, ~90% off the repeated prefix. Free to adopt; verify `cache_read_input_tokens` > 0 or it isn't working.
2. **`effort: "low"` on chat turns** — cuts adaptive-thinking spend on the 90% of turns that are dispatch and short answers; raise per-call for hard questions.
3. **Haiku 4.5 as the loop model** ($1/$5 → ~$0.008/turn, ~$2.50/mo at measured usage). Only after benching it on the Phase 1 harness — judgment under a long tool prompt is precisely what failed on the 8B, and saving ~$5/mo is not worth re-importing that failure class. Escalate-to-Sonnet as a tool if adopted.
4. **Local Qwen** — retired. Not an efficiency path. Cursor is the alternate to Sonnet.
5. **Claude Max subscription via `claude` headless** — $0 marginal API cost, but adds process-spawn latency to every voice turn and couples a launchd daemon to an interactive login. Not worth it against a single-digit monthly bill.
6. **Batch API** (50% off) — inapplicable; conversation is interactive.

**Bottom line: at measured volume, Sonnet-as-brain costs roughly a coffee per month. The efficient path is caching + low effort, not a smaller model.** Revisit model choice only if usage grows ~20× — and then bench Haiku 4.5 on the harness before switching, exactly as the 8B should have been.

## Verification discipline (every phase)

- Drive `/api/session/{id}/say` against a scratch `BRUTUS_STATE_DIR` — never the live store (the 64-junk-todos incident).
- Assert wiring, not presence: each new tool needs a test that fails when the call site is deleted.
- Log per-turn `usage` (input/cached/output tokens, latency, rounds) into turn `meta` so cost and latency claims stay checkable from the session store itself.
- The liveness probe must exercise the same configuration the chat path uses — no more thinking-off health checks blessing a thinking-on product.
