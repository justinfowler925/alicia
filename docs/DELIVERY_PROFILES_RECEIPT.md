# Local delivery-profile execution receipt

Date: 2026-09-15. Executor: Forge (Codex). Environment: isolated local Brutus
clone, branch `codex/task-delivery-profiles`, base `329368c`.
Canon reference: `2b914b18-e334-4d3d-8cb7-fa89b612043b` (supplied reference only;
no Canon access, rebinding, or duplicate work creation in this execution).

This is an implementation/test receipt, not independent acceptance. The operator
owns review, merge and deployment. No actual Brain policy or launcher files were
edited. The repository's existing `.codex/delivery.yaml` remains schema1.

## Changes

- Explicit schema2 loader validates common requirements and every named profile;
  selection is mandatory. Unknown fields, malformed structures, duplicate ids
  and missing required test/git gates fail closed. Schema1 keeps its raw-byte
  digest, defaults and legacy receipts.
- The selected profile persists in `WorkItem.delivery_policy_profile`. Its digest
  binds raw policy bytes and profile identity. Profile-bound receipts must carry
  the bound digest and correct work-item source; conflicting repositories,
  stale receipts, wrong artifacts/targets, failed/unverified results still fail.
- CLI capture identity can truthfully say `codex` / `worker`; it never substitutes
  for the owner principal. Store identity checks and lifecycle approvals remain.
- Usage: [DELIVERY_PROFILES.md](DELIVERY_PROFILES.md), including a launcher versus
  Salesforce policy with Salesforce-only sandbox gates retained.

## Commands and measured results

All Python runs used this clone's `.venv/bin/python` (Python 3.14).

```sh
.venv/bin/python -m pytest -q tests/test_delivery_profiles.py \
  tests/test_workflow_control.py tests/test_canon_cli.py \
  tests/test_canon_store_hardening.py tests/test_workflow_http.py
./scripts/verify-workflow-control.sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q --continue-on-collection-errors
```

- Focused: **103 passed**, one Starlette/AnyIO deprecation warning. The initial
  test-authoring run had two test errors (unsupported transition keyword and
  expected ordering); both were corrected. Final restored-source run also has
  **103 passed**.
- Workflow verification: **exit 0**, both Ruff lanes pass, shell syntax and plist
  checks pass. The new test file is included in this existing script.
- Full pytest: **exit 2**, three collection errors:
  `test_livekit_agent.py`, `test_livekit_owner_window.py`, `test_livekit_voice.py`
  cannot import `livekit` in the supplied environment.
- Diagnostic continuation: **exit 1**, **962 passed, 31 failed, 3 collection
  errors**, one warning. This is not a passing full-suite gate.
- A local `git archive HEAD` snapshot was used to rerun the 31 failing node ids:
  **28 failed, 3 passed**. The reproduced failures cover backend, brain,
  conversation and voice-client tests. The three full-run voice-eval failures
  report missing `livekit`; they passed when run in the baseline subset, so
  that subset does not establish their full-suite ordering behavior.
- Seven temporary guard-removal controls each produced **pytest exit 1 / one
  failed test**: removing a common gate, duplicate-id rejection, mandatory-git
  rejection, profile-digest comparison, work-item source comparison, owner
  attachment authorization, and truthful worker-kind recording. Every mutation
  was restored byte-for-byte before the final focused run.
- `git diff --check`: passes.

Full-suite and final focused runs use a temporary `sitecustomize.py` that redirects
Python home expansion to a scratch home and denies socket connections. State and
Canon paths point to `/private/tmp/forge-dod-profiles-checks/`; the real Canon
store is not used. `BRUTUS_SECURITY_BIN=/nonexistent` disables credential-helper
fallback. The isolation does not change the host HOME or launcher configuration.

The diagnostic full run exposed an existing backend test whose mock did not
prevent an actual Claude CLI subprocess. It exited with `unknown option
'--safe-mode'`; no model result was produced. The baseline subset reproduced
that behavior. Subsequent mutation/focused checks also deny external/model CLI
subprocesses. No model switch, dependency download, push, PR, merge, deployment,
or Salesforce operation was performed.

Logs and the temporary isolation/mutation harness are retained locally under
`/private/tmp/forge-dod-profiles-checks/`: `focused-final.log`, `full-pytest.log`,
`full-continued.log`, `baseline-failures.log`, `failed-nodes.txt`, and seven
`mutation-*.log` files. This directory is scratch evidence, not a durable service.

## Unfinished verification

The full-suite gate remains blocked by the reported dependency/other suite
failures. Operator review must address the environment and unrelated failures
and rerun the full suite before acceptance or deployment. No production gate was
weakened or bypassed to make this receipt appear green. Project-knowledge MCP was
unavailable; its SSH fallback was excluded by the repository-only task scope.
