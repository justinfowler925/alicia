# Alicia UI — Shine audit build plan

> **Historical scope:** For the current voice, session-continuity and release direction, use the [Apple accessibility reset plan](APPLE_ACCESSIBILITY_RESET_PLAN.md). This document remains evidence of its dated work; it does not override the reset requirements or prove current voice acceptance.

**Audit date:** 2026-08-08  
**Live SHA at audit:** `215e8e6`  
**Surfaces:** `/session`, `/mobile`, Ops `/` (`ui.py`)  
**Method:** shine audit rubric + `node ~/Projects/shine/verify/measure.mjs` + Playwright geometry  
**Verdict:** polish (`/session`, `/mobile`) · redesign-tokens (Ops `/`)  
**Repository:** https://github.com/justinfowler925/alicia

---

## Evidence (measured)

| Probe | Result |
|-------|--------|
| `/session --dark` axe | 0 violations, 1 incomplete |
| `/session` contrast | FAIL — Delete **4.03:1**; several Ideas `<p>` worst� FAIL — Delete **4.03:1**; several Ideas `<p>` worst≪p5 (clipped scroll sampling) |
| Ideas Delete / Edit | `h=28px`; danger `rgb(239,68,68)` |
| Ideas vs Ledger panels | No overlap (bottoms/tops 268.9 / 284.9) |
| `/mobile --dark` axe | **2** — `landmark-one-main`, `region` |
| `/mobile` contrast (idle) | pass, worst 7.60:1 |
| Ops tokens | ~33 hex literals; no `shine-tokens.css` |

---

## Waves A–D — DONE

| Wave | Items |
|------|--------|
| A | Session Critical/Major/Minor A1–A10 |
| B | Mobile B1–B5 |
| C | Ops shine tokens, no hex, toast dismiss/focus |
| D | Ideas Retry, aria names, 40px chips, D4 documented |

---

## Former OOS — DONE (promoted; no longer deferred)

| ID | Item | Done when / evidence |
|----|------|----------------------|
| OOS1 | Ideas sort/page | `#ideas-sort`, `sortIdeas`, `#ideas-show-more` pageSize 25 |
| OOS2 | Ledger mutate on `/session` | `ledgerAnswer` / `ledgerDecide` → `/api/answer_input`, `/api/approve/{id}` |
| OOS3 | Anam/Avatar/DemoMaker/Atlas5 shine | Wave C tokens; page `<section aria-label=…>` wrappers |
| OOS4 | sync→consult + hey rewind | `tests/test_conversation.py` deep + rewind suite green |
| OOS5 | Mobile Ideas/Ledger/Thinking | sheets + SSE + CRUD; `test_mobile_oos5_*` |
| OOS6 | i18n/RTL foundation | `lang`/`dir` on html; `alicia.dir` localStorage; `[dir=rtl]` CSS |
| OOS7 | Light mode | `[data-theme=light]` tokens; `alicia.theme` toggle session/mobile/Ops |
| OOS8 | Ops board SSE | `EventSource /api/session/board/events`; no `setInterval(loadBoard` |
| OOS9 | Brand-checker | N/A for Clearspeed marketing — personal Alicia voice; no CS brand surface |
| OOS10 | measure.mjs `--shine-*` | `~/Projects/shine/verify/measure.mjs` probes shine tokens + `getClientRects` line boxes |

---

## Non-goals (still)

- Redesigning the three-column session composition into a different product
- Merging Ops and `/session` into one page
- Full multi-locale translation catalog (OOS6 is dir/lang foundation only)

---

## Verify

```sh
PYTHONPATH=. pytest tests/test_server.py tests/test_mobile.py tests/test_conversation.py -q
node ~/Projects/shine/verify/measure.mjs http://127.0.0.1:8768/session --dark
node ~/Projects/shine/verify/measure.mjs http://127.0.0.1:8768/mobile --dark
~/.alicia/app/scripts/deploy.sh
```

### Progress

| Track | Status |
|-------|--------|
| A–D | Done |
| OOS1–10 | Done (this PR track) |
| Ship | PR #32 — merge + deploy after CI |
