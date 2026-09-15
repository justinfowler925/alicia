# Brutus

> **Current replacement direction (2026-09-07):** [Apple accessibility reset plan](docs/APPLE_ACCESSIBILITY_RESET_PLAN.md). Continuous Voice Control input and native Apple output; Siri excluded. **Gate 0 Mac proof:** `native/Gate0Proof/` (`./scripts/run-mac.sh`). Physical samples + iPhone/Xcode still open. Older architecture and completion claims below are historical where they conflict.

**Justin’s standalone MacBook coworker.** Voice is the primary work surface.
Brutus uses explicit Cursor, Claude, and Codex profiles, reads current work
directly from Linear, and keeps capture, Canon, Zoom, notes, and session state
local. Atlas is intentionally ignored: no health
probe, board fallback, chat tool, mutation, UI poll, or tunnel is active.

**Canon model and collaborator runbook:** [`brutus/canon/README.md`](brutus/canon/README.md)

## Setup

```bash
cd ~/Projects/brutus
git switch main && git pull --ff-only
bash scripts/install-brutus.sh
# → venv, MCP entry, standalone Brutus service
brutus health
bash scripts/open-operator.sh   # http://127.0.0.1:8768/ — work surface (focus queue + charts + working set)
```

### Voice brain and work supervisor

Cursor handles the low-latency conversation profile. Claude judges incremental
Codex, Cursor, and Claude session evidence into a goal, verified progress,
blocker or decision, and one recommended action. Codex `gpt-5.6-sol` handles
frontier Unfog passes; builder work stays with the explicitly selected agent.
Providers never silently substitute for one another.

The supervisor persists transcript cursors and reassesses only changed work.
Normal progress is silent. Approval, failure, a blocker, conflicting work,
stale unfinished work, or a named completion follow-up can earn an interruption.
New work first passes through the pure Unfog compiler: continue matching
inflight work, update an exact open ticket, or draft one gated Linear issue.
Frontier calls and ticket creation execute only after the reviewed proposal is
approved.

### Workflow control

The `brutus workflow` surface joins that compiler to Canon, saved Git projects,
repository delivery policies, product-feedback batches, and a reproducible
efficiency scorecard. It does not create a second queue.

```bash
brutus workflow route "Fix Brutus delivery" --repo brutus
brutus workflow route "Fix Brutus delivery" --repo brutus --create
brutus workflow policy . --bind <canon-work-item-id>
brutus workflow status <canon-work-item-id>
brutus workflow delivery <canon-work-item-id>
brutus workflow feedback --input feedback.json --create
brutus workflow scorecard --days 7

# Attach a structured owner-verified receipt to the bound policy.
brutus workflow receipt <work-item-id> --requirement-id tests \
  --type run_output --content-ref 'pytest: 817 passed' --result pass \
  --artifact-digest <git-sha>
```

The same operations are available through MCP as `brutus_work_route`,
`brutus_work_status`, `brutus_work_event`, `brutus_feedback_batch`, and
`brutus_workflow_scorecard`. Bounded external adapters post idempotent events
to `POST /api/workflow/events` with `X-Brutus-Adapter-Token`; that token cannot
accept, close, approve, or otherwise change Canon lifecycle state.

Every repository can declare its required tests, lint, visual proof, Git,
deployment, production readback, and rollback evidence in
`.codex/delivery.yaml`. Once a policy is bound to a Canon Work Item, the item
cannot leave validation while a required receipt is missing, stale, failed, or
bound to another artifact digest.

`config.yaml`:

```yaml
atlas_enabled: false
cursor_runner:
  enabled: true
  model: "composer-2.5"
  reasoning_root: "~/.brutus/app"
claude:
  enabled: true
  model: "claude-sonnet-5"
```

### Cursor MCP helpers

Legacy Atlas MCP helpers remain tracked under `scripts/cursor-mcp/`, but the
Brutus runtime neither installs nor calls them in standalone mode.

### Canon database durability

`CanonStore` uses SQLite WAL mode with a five-second busy timeout. This lets
readers run during a write and makes brief contention between Atlas dispatches
retry rather than fail immediately, but SQLite still serializes writers. If
dispatch volume becomes sustained or the timeout becomes routine, move Canon to
Postgres in a dedicated future ticket rather than stacking more retries.

Canon evidence and approvals are system-of-record data. Use
`CanonStore.backup("/secure/backups/canon-YYYY-MM-DD.sqlite")` after significant
approval/evidence writes and at least daily (or more frequently for the needed
recovery-point objective); it uses SQLite's online backup API, so do not replace
it with copying the live `.sqlite`, `-wal`, and `-shm` files. Keep backups
separately from the live database and periodically test
`CanonStore.restore(backup_path, restore_path)`, which returns an open,
migrated store.

### Canon Watches

`Watch.trigger_condition` uses a deliberately small v1 grammar: a canonical
state name such as `review`, or `state==review`. A Watch targets one Work Item
id and is evaluated after that Work Item is saved. Its durable
state-history-position marker prevents duplicate delivery on repeat saves while
allowing a later re-entry to the same state to notify again.

Slack Watch channels use either a direct Slack incoming-webhook URL or
`slack:<channel>` / `slack://<channel>` together with the
`BRUTUS_SLACK_WEBHOOK_URL` environment secret. Use `brutus canon watch list`,
`brutus canon watch show <id>`, and `brutus canon watch test <id>` to inspect
and debug Watch delivery. Canon Watches are Slack-only in v1; unsupported
channel types are rejected when the Watch is created.

Canon HTTP mutations require the local owner token. Run `brutus owner-token`,
open Canon, choose **Authenticate owner**, and paste it. Brutus exchanges it for
an HttpOnly, SameSite-strict eight-hour session; only its CSRF value stays in
that browser tab. Read-only Canon views do not require authentication.

### Canon backup operations

`com.clearspeed.brutus-canon-backup` runs the SQLite online backup each day at
03:15 into `~/.brutus/backups/canon`, retains 14 days, and writes a SHA-256
sidecar. Verify the newest backup with
`~/.brutus/app/.venv/bin/python ~/.brutus/app/scripts/canon-backup.py verify`;
verification restores into a temporary directory and never touches live state.

### GitHub Evidence ingestion

Brutus is loopback-only, so production uses
`com.clearspeed.brutus-canon-github` to poll the authenticated GitHub API every
five minutes. PR and workflow ids, repository, SHA, and the derived delivery id
are persisted on Evidence. The inbound `/webhooks/github` route is retained for
future use but fails closed without a valid HMAC signature and delivery id.

## Commands

```bash
brutus health
brutus digest
brutus brief
brutus reconcile
brutus ingest-linear
brutus register "Fix X" --id REV-61
brutus chat "what's open"
brutus dispatch --dry-run
brutus frontier
brutus cursor
brutus approve REV-61
brutus mcp                 # stdio MCP for Cursor
```

## Design: everything comes from shine

Three screens, one token layer. `/` (the board) is served from `brutus/ui.py` —
HTML and CSS inside Python string literals — while `/session` and `/mobile` use
`brutus/static/*.css`. All three read the same vendored tokens.

```bash
scripts/sync-shine-tokens.sh            # re-vendor brutus/static/shine-tokens.css
scripts/sync-shine-tokens.sh --check    # exit 1 if the copy is behind shine
node ~/Projects/shine/hooks/design-lint.mjs brutus/ui.py brutus/static/*.css
```

Brutus has no build step, so `brutus/static/shine-tokens.css` is a **copy** of
`shine/tokens/dist/personal/artifact.css`. Never hand-edit it; re-vendor with the
script. `shine/verify/doctor.mjs` calls the `--check` mode, so a stale copy fails
shine's own checks rather than going unnoticed.

**No raw values.** Colour is `var(--shine-color-*)` or `color-mix()` on one; type
is `var(--shine-text-xs|sm|base|lg|xl|2xl)` with `var(--shine-leading-*)`; radius
is `var(--shine-radius-sm|md|lg)`; spacing is `var(--shine-space-1…8)`. The lint
hard-blocks a literal `font-size` or colour on both Cursor and Claude Code, on
edit and again at end of turn. A missing token is a token gap — add it in shine
and rebuild; do not invent a local scale here, which is how these three screens
once carried 5, 7 and 12 different font sizes between them.

**The remaining ~30 lint notes on `ui.py` are deliberate.** They flag layout
dimensions that are not spacing — the 300px rail, the 900px breakpoint, flex
bases like `78px`/`52px` on the id and age columns. Notes never block. Do not
silence them with a blanket `shine-lint: off`: that pragma also switches off
colour and type for the whole file, which is exactly how `mobile.css` went
unchecked for months.
