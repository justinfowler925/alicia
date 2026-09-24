# Nucleus command center contract

> **Historical scope:** For the current voice, session-continuity and release direction, use the [Apple accessibility reset plan](APPLE_ACCESSIBILITY_RESET_PLAN.md). This document remains evidence of its dated work; it does not override the reset requirements or prove current voice acceptance.

## Outcome

Opening Alicia at `/` answers one question first: **what needs Justin across every
project, Linear issue, and Codex/Cursor/Claude task?** Each project row carries the
source records behind its rank, and the same operating graph is available to the
plain-English brain.

## Authority and target

- Runtime target: the isolated Alicia service worktree deployed by
  `scripts/deploy.sh`, currently `~/.alicia/app`.
- Linear owns issues; Git owns checkout state; Codex, Cursor, and Claude own their
  native tasks; Atlas is an execution overlay; Nucleus owns company operating facts
  and append-only receipts.
- Alicia normalizes, links, ranks, and deep-links. It does not copy source records
  into a competing ledger or claim an external mutation without rereading its source.

## Measured premise

The deployed artifact at the start of this work was `49116d2`. It exposed 67 Git
rows (19 at risk), 91 Atlas open items, 13 recent Cursor/Claude rows, and no Codex
rows. Codex's current local catalog held 220 tasks. The three populations had no
shared project identity or joined view.

## Scope and preservation

This increment delivers the read/control spine:

1. Normalize Git workspaces by remote project identity.
2. Add Codex's current SQLite catalog to the read-only agent inventory while
   preserving Cursor and Claude history.
3. Build one deterministic operating-graph snapshot joining projects, live Linear
   issues, agent tasks, and Atlas overlays, with explicit unmapped/partial counts.
4. Add the Nucleus data-grid page as Alicia's default daily ritual.
5. Give the canonical conversation brain the same Nucleus read and proposal-gated
   local project/thread organization tools.

Existing Work, Canon, Notes, Avatar, Demo Maker, voice, approval artifacts, state
stores, and source-specific UIs remain available. This increment does not send agent
messages, archive native tasks, mutate Linear, or connect Nucleus production data.

## Acceptance

- Project population is non-zero and duplicate worktrees with the same remote share
  one project id; risk evidence remains attached to each workspace.
- Codex, Cursor, and Claude all appear in the normalized agent contract; the proof
  fails if any expected source has a zero denominator in its fixture.
- Linear pagination preserves issue UUID, identifier, project UUID/name, assignee,
  state, URL, priority, and update time. Missing credentials or network failures
  render a partial/error source state, never an empty success.
- The `/api/nucleus` snapshot includes source counts, mapping coverage, deterministic
  attention reasons, and an explicit unmapped bucket.
- Screen and chat call the same snapshot builder.
- The Nucleus grid provides search, filters, clear path, sortable headers, column
  visibility, resize, row selection, pagination, row actions, and loading/empty/
  filtered-empty/error/populated states.
- Selecting a project reveals its source tickets and agent tasks; Ask Alicia scopes a
  plain-English prompt to the exact project id.
- Local keep/park/focus operations executed through conversation are exact stored
  artifacts, single-use, and return the written overlay.
- Full tests pass; Shine measure and compare exit zero against the rendered candidate;
  the deployed `/version` SHA and process cwd identify the landed artifact.

## Delivery

Branch → tests and browser proof → PR → sync with current `main` → CI → merge →
deploy with `scripts/deploy.sh` → verify `/version`, process cwd, source counts, and
the primary browser workflow.
