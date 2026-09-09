# Brutus UI build plan

> **Historical scope:** For the current voice, session-continuity and release direction, use the [Apple accessibility reset plan](APPLE_ACCESSIBILITY_RESET_PLAN.md). This document remains evidence of its dated work; it does not override the reset requirements or prove current voice acceptance.

**Source audit:** [`docs/UI_UX_AUDIT.md`](UI_UX_AUDIT.md) (2026-08-02)  
**Recheck:** 2026-08-02 evening — `main` after Waves 2–5  
**Shipped:** 2026-08-02 — Phases A–C in `brutus/ui.py`; Phase D HTML smokes in `tests/test_server.py`  
**Ship commit (main):** `1ffc761` — *Ship operator UI build plan Phases A–C*  
**Target:** `brutus/ui.py` embedded SPA — vanilla HTML/CSS/JS, no React rewrite  

## Status

| Phase | Focus | Status |
|---|---|---|
| **A** | A11y + mobile shell | **Done** |
| **B** | Confirms, forms, data triad | **Done** |
| **C** | List chrome (shared toolbars) | **Done** (C8 density deferred) |
| **D** | Verify & ship | **Done** (HTML smoke + marker asserts) |

---

## Surface map (8 pages)

| Page | Nav | Notes |
|---|---|---|
| Work | primary | Toolbar + Needs you / Stuck / bots / queued |
| Agents | primary | `.filt.list-bar` + Keep/Park/Promote |
| Notes | primary | Capture + kanban + Lessons |
| Chat | always-on / mobile sheet | Expand · Live · Speak |
| Atlas5…Projects | More sheet (mobile) / rail (desktop) | Toolbars where planned |

**Mobile tabs:** `Work · Agents · Notes · Chat · More`

---

## Phase A — A11y & mobile shell

- [x] **A1** Mobile shell — bottom tabs, More/Chat sheets, `#chatdock` reparent, rail `display:none` ≤900px, `chatbig` ≥901px  
- [x] **A2** Semantic nav — `nav-item` buttons + `aria-current`  
- [x] **A3** Stuck accordion — button + `aria-expanded` / `aria-controls`  
- [x] **A4** Global `:focus-visible`  
- [x] **A5** Labels (`sr-only` / `aria-label`) including lessons + agents filter  
- [x] **A6** Fake links → `.linkish` buttons  

---

## Phase B — Confirms, forms, data triad

- [x] **B1** `confirmAction` / `withBusy` / inline-err / modal  
- [x] **B2** Confirms: Reject, restart, steerRestart, delTodo, avatar apply/delete, Park, To bots  
- [x] **B3** Inline errors on mutations; toast secondary  
- [x] **B4** Triad for board, projects, todos, agents, lessons  
- [x] **B5** Delete `aria-label`; tag editor modal (no `prompt`)  
- [x] **B6** Chatbots / Demo Maker Live·Down·Unknown  

---

## Phase C — List chrome & polish

- [x] **C1** `listToolbarHtml` / filter / sort + Agents `filt list-bar`  
- [x] **C2** Work toolbar + Needs you search-only + Working/Queued sort  
- [x] **C3** Stuck filter  
- [x] **C4** Projects toolbar  
- [x] **C5** Atlas5 toolbar  
- [x] **C6** Sticky `.sech`  
- [x] **C7** Toast `aria-live` + hover pause  
- [ ] **C8** Density toggle — **deferred** (non-goal for this ship)  

---

## Phase D — Verify & ship

- [x] HTML smoke: `test_home_ui_build_plan_markers`  
- [x] Existing home/agents tests green  
- [x] No `prompt(`; no `<a onclick=` action fakes  

Manual (operator, optional): 375px tabs, confirm Park/Reject, kill-API Retry, poll input survival.

---

## Non-goals (still)

- React / design-system extraction  
- Full DataGrid (resize/CSV/virtualize)  
- C8 density toggle  
- Clearspeed marketing restyle  
