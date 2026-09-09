# UI/UX Audit: Brutus operator SPA

> **Historical scope:** For the current voice, session-continuity and release direction, use the [Apple accessibility reset plan](APPLE_ACCESSIBILITY_RESET_PLAN.md). This document remains evidence of its dated work; it does not override the reset requirements or prove current voice acceptance.

**Date:** 2026-08-02  
**Recheck:** 2026-08-02 evening — after Waves 2–5  
**Remediation:** 2026-08-02 — Phases A–C shipped in `brutus/ui.py` (see [`UI_BUILD_PLAN.md`](UI_BUILD_PLAN.md))  
**Surface:** Product / app — FastAPI SPA in `brutus/ui.py` (`http://127.0.0.1:8768/`)  
**Rubric:** ui-ux skill (MUST + SHOULD for app shells)  
**Live canvas:** Cursor project canvases → `brutus-ui-audit.canvas.tsx`

> **Status:** Original Critical/Major gaps below are addressed by the build plan ship (mobile shell, semantic nav, confirms, triad, list toolbars). Audit text retained as historical record.

## Verdict

**Polish** — craft and operator voice are strong; completeness (a11y, mobile shell, confirms, data triad, list chrome) fails several MUST bars.

## Summary

The operator empty copy, section jobs, alarm banner, poll-safe input restore, and chat busy/voice states are unusually good for a single-file SPA. Gaps are accessibility, mobile (rail `display:none` under 900px), destructive confirms, loading≠empty≠error, and DataGrid-class list chrome on Work / Projects / Atlas5.

## Top issues

| # | Severity | Issue | Contract/rule | Fix |
|---|---|---|---|---|
| 1 | Critical | Mobile: entire right rail (nav + chat + bots) hidden below 900px with no replacement | Sidebar MUST — mobile drawer | Bottom tabs / More sheet + Chat sheet; never hide the only nav |
| 2 | Critical | Nav links are bare `<a onclick>` with no href | Link MUST / fake links ban | `<button>` or real anchors; focus-visible |
| 3 | Critical | Stuck accordion headers are clickable divs — no ARIA / keyboard | Accordion MUST | Button trigger + `aria-expanded` / `aria-controls` |
| 4 | Critical | Destructive actions have no confirm (Reject, Start over, Start all N, Delete note/config, Avatar replace) | Destructive confirm MUST | Confirm dialog |
| 5 | Critical | Focus outlines removed (`outline:none`) without full ring | Foundations — focus-visible | `--ring` on `:focus-visible` |
| 6 | Critical | Placeholder-only labels (chat, answer, notes, avatar name) | Input MUST | Visible or `sr-only` / `aria-label` |
| 7 | Major | Work/Projects/Atlas5 lists lack search/sort/sticky headers | Table/DataGrid MUST (app) | Toolbar search + sort; sticky section headers |
| 8 | Major | Loading vs empty conflated (projects/todos fail → `[]`) | Data triad MUST | Skeleton + error+retry ≠ `.none` |
| 9 | Major | Toast is only error channel for mutations | Toast MUST | Inline errors; toast secondary |
| 10 | Major | `busy` blocks double-submit but buttons show no loading | Button loading MUST | Disable + `aria-busy` + label |
| 11 | Major | Chatbots status mostly a colored dot | Color-only status ban | Pair with Live / Down text |
| 12 | Major | Notes ✕ has no accessible name; tags use `window.prompt` | IconButton / Dialog | `aria-label`; tag modal |
| 13 | Major | Avatar Apply & redeploy has no confirm (replaces Anam slot) | Irreversible confirm | Confirm which avatar is replaced |
| 14 | Minor | Body 13.5px dense chrome | Typography floor | Optional density toggle later |
| 15 | Minor | Fake `<a onclick>` for Check now / self-tests / Apply saved | Fake links ban | Buttons |
| 16 | Minor | Toast lacks `aria-live`; no pause on hover | Toast MUST | `role=status` + pause |

## Completeness

| Control | Ladder | Notes |
|---|---|---|
| Navigation | Below MUST | onclick anchors; no mobile drawer; weak focus |
| Accordion (stuck) | Below MUST | div click; no ARIA |
| Work lists | Below MUST | Not DataGrid; no search/sort/page |
| Forms (answer/notes/chat) | Below MUST | Placeholder labels; no field errors |
| Buttons (mutations) | Below MUST | No loading visual |
| Dialogs | Missing | `prompt()` / no confirm |
| Toast | Partial | Works; sole-channel unsafe |
| Chat | Strong | Busy, voice, expand |
| Status / bots | Partial | Text+dot on rail; weaker on site rows |
| Empty copy | Strong | Human, specific empties |

## Composition & hierarchy

**4 / 5** — Clear focal region (Needs you), one job per section, buttons say what they do, no chart clutter. Deduct for equal-weight Approve / Reject / Start over and chatbig default consuming half the viewport.

## States coverage

| View | Loading | Empty | Filtered-empty | Error | Notes |
|---|---|---|---|---|---|
| Work board | fail | pass | n/a | partial | Load fail → thin headline; no skeleton |
| Needs you | n/a | pass | n/a | fail | Errors toast-only |
| Stuck groups | n/a | pass | n/a | fail | Bulk restart no confirm |
| Projects | fail | pass | n/a | fail | Fetch fail → “No projects found” |
| Notes kanban | fail | pass | n/a | fail | Fetch fail → empty CTA |
| Atlas5 | fail | pass | n/a | pass | Has unreachable error path |
| Avatar | fail | pass | n/a | pass | Studio error; apply progress in `#av-out` |
| Chat dock | pass | pass | n/a | pass | thinking… / Chat failed in transcript |

## Accessibility blockers

- Rail (only nav + chat) removed on narrow viewports
- Non-semantic nav and accordion triggers
- Missing labels / focus rings
- Icon-only delete without name
- Color-leaning live/down on site rows
- No confirm on irreversible bot / avatar actions

## Anti-patterns hit

Fake links · placeholder-as-label · confirm-less destructive · toast-only errors · loading≈empty · color-leaning status · `outline:none` without ring. Dark theme is intentional for this operator tool (not “dark for its own sake” slop). Hover-only row actions are **not** hit — actions stay visible.

## What already works

- Human empty states with next action
- Alarm banner for completion stalls
- Input value preserve across 20s poll rebuild (`savePageInputs` / `restorePageInputs`)
- Chat busy + Live/Speak state machine
- Escaped HTML; Linear links with `rel=noopener`
- Avatar apply streams step results inline
- Documented UX rules in `ui.py` module docstring (no charts, real titles, buttons say what they do)

## Out of scope / deferred

- React rewrite / design-system extraction
- Clearspeed marketing brand pass (personal operator tool)
- Full Carbon DataGrid (column resize, visibility, CSV, multi-select) unless lists grow large
- CLI / MCP surfaces (not browser UI)

## Follow-on

Build plan: [`docs/UI_BUILD_PLAN.md`](UI_BUILD_PLAN.md)
