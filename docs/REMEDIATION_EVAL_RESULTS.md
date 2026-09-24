# Alicia remediation eval results

> **Historical scope:** For the current voice, session-continuity and release direction, use the [Apple accessibility reset plan](APPLE_ACCESSIBILITY_RESET_PLAN.md). This document remains evidence of its dated work; it does not override the reset requirements or prove current voice acceptance.

Date: 2026-08-24

## Method

The four hostile evals in `tests/evals/test_remediation_evals.py` were written
before implementation and first run against the exact pre-change artifact at
`abab3ff`. They test the layer the operator relies on, not helper-function
presence: HTTP owner authorization, webhook origin authentication, ambiguous
Work Item matching, and operated deploy/backup machinery.

## Pre-implementation

Command used the pre-change checkout through `PYTHONPATH` with pytest importlib
mode. Result: **0/4 passed**.

1. An unauthenticated Canon mutation returned 200 instead of 401.
2. An unsigned GitHub payload returned 202 instead of 401.
3. Two Work Items sharing `REV-777` caused Evidence to attach to the first.
4. Deploy/backup operation assertions found neither a dirty-artifact gate nor
   scheduled Canon backup implementation.

## Post-implementation, local

The same four preregistered evals pass. A fifth supplemental browser-session
probe verifies that an HttpOnly owner cookie without the matching CSRF value is
rejected and the cookie+CSRF pair succeeds.

* Hostile eval file: **5/5 passed**.
* Repeated warm run: **20/20 suites passed (100/100 individual checks)**.
* Focused trust/backup/Watch set: **12/12 passed**.
* Full suite: **636/636 passed** with one upstream Starlette/httpx deprecation warning.
* Syntax: `bash -n` and Python `compileall` passed.
* Launchd definitions: both new plists passed `plutil -lint`.

## What these results do not prove yet

Local green tests do not prove deployment, launchd installation, live backup
creation/restore, GitHub CLI authentication inside launchd, a channel-side
Slack Watch receipt, or REV-490's ten human voice/UI sessions. Those require
post-merge production receipts and must remain explicitly open until measured.
