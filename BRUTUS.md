# Brutus

> **Current replacement direction (2026-09-07):** [Apple accessibility reset plan](docs/APPLE_ACCESSIBILITY_RESET_PLAN.md). Continuous Voice Control input and native Apple output; Siri excluded. **Gate 0 Mac proof app:** `native/Gate0Proof/` — run `./scripts/run-mac.sh`. Physical VC samples + iPhone (needs Xcode) still open. Older architecture and completion claims below are historical where they conflict.

Justin's right hand, running on the laptop. End state: reads email/Slack, tracks
coworker requests, drafts everything, hands work to Atlas (wish front door) —
execution stays gated behind Justin, **thinking is never restricted**. Does not
own the ledger.

> **Rule for future sessions:** never fix a chat reliability bug by narrowing
> Brutus's identity or banning cognition (see 2026-07-28). Fix the plumbing;
> keep the rails (board-authoritative context, never-invent-state, plain-text
> rendering, execution honesty).

**SSOT docs (Atlas6 repo):** [`docs/DOD.md`](../atlas6/docs/DOD.md) · [`ATLAS6.md` §7.7](../atlas6/ATLAS6.md) · **Red Team:** [`docs/RED_TEAM.md`](../atlas6/docs/RED_TEAM.md) · [`docs/AUTH_SWEEP.md`](../atlas6/docs/AUTH_SWEEP.md)

> **2026-07-28 — unlock shipped.** Work surface shows real Atlas5 `awaiting_input` as answer cards. Frontier Claude drain runs on Studio ticks. Cursor SDK runner is in-tree (`cursor_runner` — enable + `CURSOR_API_KEY`). Do not rebuild these from the stall diagnosis.

Install: `bash scripts/install-brutus.sh`  
Operator UI: `bash scripts/open-operator.sh` → **http://127.0.0.1:8768/**  
Watchdog: reconcile + dispatch when `ready>0`; optional cursor runner; never auto-approves Justin gates.

```bash
brutus health
brutus brief
brutus frontier
brutus cursor
brutus dispatch --dry-run
brutus chat "wip digest"
```

**Conversation brain:** Claude Sonnet 5 (`claude.enabled`) with Cursor as the alternate (`cursor_runner`). The local 8B is retired — do not re-enable `local_llm`.  
**Cursor runner:** enabled — `CURSOR_API_KEY` soft-loaded by `brutus-serve.sh` from 1Password Atlas (`op://Atlas/CURSOR_API_KEY/Api key`). Allowlist: `atlas6` + `brutus` only; empty/`sfdc` hints are skipped, never defaulted.

## Avatar page (2026-08-01)

One surface for the FNOL demo's identity: person × outfit × service (which
avatar vendor leads) × SMS transport, plus named saved configs. Applying
enrolls the chosen face master on Anam, updates the Vercel env and triggers a
redeploy, reporting each step honestly — a half-applied config is worse than a
failed one. Anam caps custom avatars at 3, so applying replaces one and mints a
new id; saved configs therefore store intent (master + tier), never ids.
Enrollment proxies through Atlas5 because this laptop cannot reach
`api.anam.ai`. Live state comes from the deployed app's own endpoints, not
Vercel's env API. Code: `brutus/avatars.py`, routes `/api/avatar*`.

**mflux drafts (2026-08-08):** `scripts/make-face.sh` lands PNGs in
`~/mflux-out/faces` on the Studio. The Avatar page lists them; **Stage** copies
into `faces/looks/{persona}_{look}.png`, **Stage & apply** enrolls
(delete-to-swap) in one shot. APIs: `GET /api/avatar` → `drafts`,
`POST /api/avatar/stage`.

## Demo Maker tab

Fowler Demo Maker (Voicemaker Studio on the Studio, tailnet-only) plus the
published library, with liveness from the existing sites registry.

## Front door Wave 1 (2026-08-02)

Conversation continues across UI / CLI / MCP: UI sends transcript + stable
`conversation_id`; CLI/MCP inject last-turn history from `memory.sqlite` when
none is provided; `resolve_chat_reply` runs a multi-turn TOOL:/ARGS: loop
(capped) before answering. Right-hand prompt unchanged — no kiosk narrowing.
Plan SSOT: `~/fowler-brain/strategy/plans/operator/2026-07-29-brutus-front-door.md`.

## Front door Wave 2 (2026-08-02) — Agents tab

`agent_sessions.py` scans Cursor `~/.cursor/projects/*/agent-transcripts` and
Claude `~/.claude/projects` + live `~/.claude/sessions` (read-only). UI **Agents**
tab lists recent threads with Keep / Park / To Notes / To bots. Overlay state
lives in `memory.sqlite` (`agent_pins`). Chat tools: `list_agent_threads`,
`summarize_agent_thread`. APIs: `GET/PATCH /api/agents`, `POST …/promote`.

## Front door Wave 3 (2026-08-02) — Backend tools

`ask_cursor` → one-shot Cursor SDK on allowlisted cwd (`run_cursor_chat`; default
`repo_hint=brutus`). `ask_claude` → Anthropic Messages API (`claude.py`; key from
`ANTHROPIC_API_KEY`, soft-loaded by `brutus-serve.sh`). `ask_atlas6` slimmed for
actionable replies; Studio-down returns `atlas6_unreachable` + Cursor/Claude hint.

## Front door Wave 4 (2026-08-02) — Factory flow from chat

Chat tools: `get_digest`, `dispatch_tick` (dry_run default), `reconcile`,
`answer_steering`, plus existing `register_thread` / `get_work_surface`.
Probe-filtered by default. Completion alarm is first-line in chat context, a
sys line in the dock, and a banner on every page.

## UI/UX audit pass (2026-08-04)

Audited the live surface against the `ui-ux` skill's contracts, then fixed the
list. Four things worth not re-learning:

1. **All `max-width:900px` overrides live in ONE block at the very end of the
   stylesheet.** They used to sit above the base `.rail{display:flex}` rule —
   same specificity, earlier in source, so `.rail{display:none}` *lost* and the
   rail rendered as a full extra 375×812 screen of duplicate nav below the board
   on phones, while `matchMedia` told the JS it was mobile. Guarded by
   `test_mobile_rail_rule_wins_on_source_order`, which asserts the *order*, not
   that the rule exists — asserting existence is what let it survive.
2. **A gate reason is never truncated without a way to read the rest.**
   `human_reason` returns the full text; `focus.clip()` cuts on a word boundary
   and marks it with `…`; decide rows carry `why_full` and the card offers
   "Read the full question". Approve/Reject on a half-sentence was the worst
   thing on the board.
3. **Sort the number, never the phrase.** `age_minutes` is on every row.
   String-comparing "17h" vs "5d" ordered `11d, 17h, 2h, 3w, 5d`.
4. **`--dim2` must clear 4.5:1 on `--bg`, `--panel` AND `--hover`.** It carries
   the age column, section counts and every empty-state sentence; at the old
   `#5c6673` it was 2.75:1 on a hovered row.

Also: every overlay goes through `openModal`/`closeModal` (focus trap, focus
restore, inert background, scroll lock) — including the mobile sheets; lists are
paged at 25 with an honest "showing N of M"; page state lives in the URL hash so
Back works; the four late pages (atlas5/sites/avatar/demomaker) got the
loading/empty/error triad instead of mapping a failed fetch onto an empty list.

## Front door Wave 5 (2026-08-02) — Right-hand memory loop

Notes pad via chat: `list_notes` / `capture_note` / `promote_note` (todo→register).
Working notes search. Local `lessons` table + `draft_lesson` / `list_lessons`
(laptop only — never auto-email/Slack). Notes UI shows a Lessons section.
Chat recipes: `capture: …`, `promote <id>`, `lesson: title | body`.
**Front door plan complete** (Waves 1–5).

## Mobile conversation surface (2026-08-08)

Phone card at **`http://127.0.0.1:8768/mobile`**. Layout + reconnect contract
forked from `clearspeed-demos/public/demos/fnol` (sessionStorage resume with a
15-minute freshness window, live capture sheet, splash → live → reconnect/outro).
Transport is Brutus `/api/session/*` — never Anam. Snap key is
`brutusMobileResumeSnapshot` so it cannot collide with the FNOL demo.
**Never write back** to `public/demos/fnol/`. Assets: `brutus/static/mobile.*`.

## Zoom AI Companion → Inbox (2026-08-11)

Justin's own meetings now reach the pad. There were already three Zoom pieces —
`scripts/feed_zoom_to_brutus_notes.py`, its wrapper, and
`com.clearspeed.brutus-zoom-notes` — and they work, but they read Salesforce
`Meeting_Notes__c`, which the Apex pipeline only fills from
`recording.completed` webhooks. **He does not cloud-record** (`recordings_list`
over a month: zero), so that job scanned 600+ of other people's notes and posted
0 every hour, correctly. His meetings carry AI Companion output instead.

New lane, additive, nothing removed:

* `brutus/zoom_api.py` — Server-to-Server OAuth. The credentials already existed
  (`ZOOM_ACCOUNT_ID` / `_CLIENT_ID` / `_CLIENT_SECRET`, same ones
  `sfdc/salesforce/scripts/zoom/backfill_summaries.py` uses) with exactly the
  right scopes: `meeting:read:summary:admin`,
  `meeting:read:list_summaries:admin`, `meeting:read:list_past_participants:admin`.
  Env first, 1Password via secrets_softload — never Doppler. Never bare
  `op read`, which hangs on Touch ID under launchd.
* `brutus/zoom_ingest.py` — extraction, dedupe, ledger. Reads both the REST
  shape (`next_steps` as a flat `"Owner: text"` list) and the connector shape
  (markdown `## Action Items` / `## Next steps`).
* `POST /api/zoom/poll` — fetch + ingest, inside the daemon. `GET|POST
  /api/zoom/ingest` for status and for pushing already-fetched payloads.
* `scripts/zoom-poll.sh` + `com.clearspeed.brutus-zoom-summaries` — hourly.

Items land at stage **Captured** with `source="zoom"`, so the refine sweeper
drafts titles for them like any other capture. `raw` keeps the verbatim
`Owner: text [Topic, date]` plus the Zoom doc links, so a redrafted card still
says where it came from.

Decisions worth not re-litigating:

* **One section per meeting, not both.** "## Action Items" and "## Next steps"
  are two AI restatements of one conversation; reading both duplicated a third of
  a real meeting (16 items, 5 of them the same commitment twice). Fuzzy matching
  was measured and rejected, not skipped: token overlap puts genuine duplicates
  at 0.136–0.385 and genuinely distinct items from 0.158 up, so every threshold
  both keeps duplicates and merges separate work — and merging two real tasks
  loses a commitment. My Notes wins when it has items; the summary stands in when
  it does not. `mode="both"` opts into the overlap.
* **Zoom's "nothing to report" prose is not a task.** "No action items assigned."
  and "Next steps were not generated due to insufficient transcript." arrive as
  ordinary bullets.
* **A "not mine" verdict is remembered.** The summaries API is account-wide (609
  in a week); 31 were his. Deciding a meeting is somebody else's costs a
  participants call, so `zoom_not_mine` keeps the verdict. The ledger is read
  once per poll and the verdicts written in one commit — asking per meeting
  opened ~1,200 sqlite connections against the file the refine sweeper is
  writing, and under the default journal mode a writer blocks readers. Measured
  against live Zoom: **cold 117 s, warm 3 s** (609 listed, 31 his, 50 items).
  The cold tail is bookkeeping — his meetings are ingested first, so the pad
  fills early, and nothing waits on a launchd job.
* **Own tables, never a column on `todos`.** `zoom_meetings`, `zoom_items`,
  `zoom_not_mine`. Adding a column to a shared table is the 2026-08-08 outage;
  new tables are additive because no older reader selects from them.
* **The two lanes use different UUIDs for the same meeting** (`8ZHzz9…==` via
  REST, `F191F3CF-…` via the connector), so running both over one meeting
  double-captures it. REST is the automatic lane; the connector script is for
  ad-hoc pulls.

Owner scope: `owners=["justin"]` keeps items he owns, items with no named owner,
and items that *name* him ("Nicole: Share TowBook access with Justin") — his
dependency in someone else's 1:1. Drop `owners` to capture everything.

### My Notes transcript fallback (2026-08-27)

Zoom can save the live transcript and still leave the finished My Notes page
empty. The meeting-summary API cannot recover that personal transcript. Zoom's
My Notes API can: `GET /my_notes/notes/{noteId}/content?include=transcript`.

`POST /api/zoom/my-notes/poll` therefore reads Justin's personal notes, verifies
the token owner is `justin.fowler@clearspeed.com`, and uses Zoom's generated
Markdown when it exists. When the generated page is blank but the transcript is
present, Brutus's normal one-shot brain creates the recap. A note with neither
content nor transcript remains pending and is retried; it is never recorded as a
successful empty note. Each completed note produces one dated recap in Ideas and
separate owned action items. `com.clearspeed.brutus-my-notes` polls every five
minutes; unchanged notes do not refetch their transcripts.

The Server-to-Server Zoom app needs three owner-scoped read permissions:
`my_notes:read:note`, `my_notes:read:content`, and `user:read:user:admin`. The last is
the principal check; without it Brutus cannot prove whose private notes the
owner-scoped endpoint is returning.
