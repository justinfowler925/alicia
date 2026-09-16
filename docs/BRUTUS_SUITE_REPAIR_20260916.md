# Brutus suite repair — 2026-09-16

Canon work: `127699ef-5ed6-4e35-942c-ff1b6cce408d` (reference only; Canon was not accessed).
Environment: local-only standalone clone on jf-studio; executor: Forge.
Base: `7c6c362aff98e910e4a05b315e028067b7a3777a`, branch `codex/full-suite-repair`.
This is a source execution receipt, not independent acceptance or a deployment claim.

## Result and required operator work

The 29 supplied failures are repaired in the five affected modules: **142/142 pass**
in the final full-suite run (121 existing cases plus 21 added cases). No tests were
removed, skipped or xfailed. The unchanged workflow-control tests (37), delivery-profile
tests (54), and Canon hands tests (5) also passed in the full run; the three workflow
HTTP tests remain among the blocked database cases. The full-suite acceptance requirement is **blocked**:
**924 passed, 81 failed, 32 setup errors, 0 skipped**, 1 existing Starlette deprecation
warning, 1037 collected, 13.30 seconds. All 113 failure/error messages in JUnit are
`sqlite3.OperationalError: attempt to write a readonly database`.

The mandated `BRUTUS_STATE_DIR` is outside this session's writable roots. The initial
unmodified run under the same restrictions returned **116 failed, 837 passed,
63 setup errors, 0 skipped** in 13.28 seconds. This is different from the operator's
29 failed / 987 passed baseline, because the sandbox cannot write the operator's
private test-state databases. The isolation guard and those external files were
not edited, replaced, or bypassed. The test environment variables were not changed.
Only the repaired module fixtures use explicitly injected per-test stores; the
process-wide state fixture and production state resolution remain unchanged.

Operator review MUST rerun the exact full-suite command below in an authorized
local-only environment where the specified private test directories are writable.
It must reach zero failures/errors with zero new skips before acceptance or any
production delivery decision. Preserve the existing production controls. The live
launchctl/process check denied by the supplied isolation guard also remains a
REQUIRED operator verification: the mocked service API test is not evidence of
host process discovery, cancellation, or deployment behavior. Do not bypass the
guard to perform it. No live voice/browser/provider behavior was verified here.
No push, install, dependency change, deployment, credentials, SSH, Canon mutation,
runtime-state access or intake submission was performed. This record holds the
carry-forward because live intake access is prohibited for this assignment.
Project-knowledge was unavailable as supplied; current repository source was used.
No repository AGENTS.md was present; supplied operating rules were followed.

## Causes and repairs

1. **Backend discovery (1 failure): stale fixture.** `ask_claude` searches three
   fixed binary locations after PATH lookup fails. The missing-CLI test mocked only
   PATH, so a host CLI remained discoverable and the isolation guard denied launch.
   The fixture now marks every discovery location absent. Production discovery is
   unchanged; the existing success test still verifies CLI argv and disabled tools.
2. **Brain (22 failures): stale setup plus production defects.** Tests intercepted
   native `_create` but their config selected the new default CLI transport. They
   now explicitly select API, opt in and supply a private absent kill-file fixture.
   Production incorrectly tried Cursor before the selected API, or after CLI failure,
   and overwrote useful errors/cap results. Routing now calls exactly the selected
   Claude transport; blocked API configuration refuses rather than silently selecting
   CLI. Cursor-only one-shot `complete()` routing is unchanged. CLI completions now
   enter the same guarded tool loop as API responses: proposal claims, invented
   tickets, required thread search, tool allowlists, voice restrictions and round cap
   all apply. A failed proposal *attempt* no longer backs a claim of a stored artifact;
   an actual successful artifact id is required. Voice deterministic recovery remains
   available; text failure remains explicit, and authentication details stay out of
   spoken replies. Stable system prompt caching is separate from voice/standing/offer
   context. Two neighboring tests used retired Atlas reads and could pass without
   observing a real tool result; their fixtures now use an offered local read.
   The old 45-word source assertion now checks the existing 60-word voice contract.
3. **Conversation (2 failures): stale return shape.** `execute_artifact` returns a
   result object, not a string; outbox completion reads `.reply`. Fixtures now return
   that shape and verify persisted outbox status/reply for verified voice and typing.
   The production owner-verification gate is unchanged. Stores/outbox are scoped to
   each test and worker threads are joined before fixture teardown. The no-pending
   voice test mocks the brain and checks both replies synchronously, preventing it
   from attempting an external provider or leaking a worker beyond its fixture.
4. **Process control (1 failure): host-dependent fixture.** The service endpoint
   test previously ran real launchctl. It now supplies isolated installed/source
   plist directories and command results at the subprocess boundary, exercising the
   real route, service enumeration and parser. It checks all three rows: running,
   loaded-but-idle, and not loaded/not installed; an unrelated Apple plist is excluded.
   The core service and exact probe denominator are asserted. Production control code
   and allowlists are unchanged. Real host behavior remains an operator check.
5. **Session client (3 failures): obsolete source-string assertions.** Provider
   summaries and lifecycle text moved to the glance renderer; `voiceOwnsPlayback`
   now covers both LiveKit and ConvAI. A Node VM executes the full shipped JS and
   calls `renderSupervisor` and SSE `handle` with a minimal local DOM. It verifies
   rendered provider identities/source text, pending versus verified/actionable
   progress, and zero duplicate speech during transport handshake or connection.
   Legacy speech is the positive control for both reply and answer events. Existing
   startVoice ordering, owner-enrollment/security and teardown checks remain.
   Client production JS/CSS/HTML are unchanged. This harness is behavioral unit proof,
   not browser geometry, microphone, audio or live transport proof.

## Commands and results

Run from `/Users/jfstudio/projects/brutus-suite-repair`. External sitecustomize
redirects expanduser to a private home and denies network and model/external
subprocesses. All pytest commands used this exact isolation prefix.

Initial reproduction (unmodified clone; output `/tmp/brutus-repair-before.log`):

```bash
PYTHONPATH=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-isolation:$PWD BRUTUS_STATE_DIR=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-test-state PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q > /tmp/brutus-repair-before.log 2>&1
```

Focused verification (142 passed, zero skips; `/tmp/brutus-repair-targeted3.log`):

```bash
PYTHONPATH=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-isolation:$PWD BRUTUS_STATE_DIR=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-test-state PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_backends.py tests/test_brain.py tests/test_conversation.py tests/test_process_control.py tests/test_session_voice_client.py > /tmp/brutus-repair-targeted3.log 2>&1
```

Final full-suite verification, including the later outbox assertions and restored
mutations (all 142 affected-module cases still passed; exit 1 solely for the 113
read-only database failures/errors):

```bash
PYTHONPATH=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-isolation:$PWD BRUTUS_STATE_DIR=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-test-state PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q --junitxml=/tmp/brutus-repair-final.xml > /tmp/brutus-repair-final.log 2>&1
```

Changed-file lint (exit 0, `All checks passed!`), Node syntax (exit 0), and workflow
control (exit 0, both lint lanes passed plus shell syntax/plist checks):

```bash
PYTHONPATH=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-isolation:$PWD BRUTUS_STATE_DIR=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-test-state PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check brutus/brain.py tests/test_backends.py tests/test_brain.py tests/test_conversation.py tests/test_process_control.py tests/test_session_voice_client.py
node --check tests/session_client_contracts.cjs
PYTHONPATH=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-isolation:$PWD BRUTUS_STATE_DIR=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-test-state PYTHONDONTWRITEBYTECODE=1 bash scripts/verify-workflow-control.sh
git diff --check
```

## Negative controls

Production mutations were applied one at a time to local source, then restored in
`finally` before any other work. No source mutation remains. Every mutation produced
pytest exit **1**, with an assertion failure that distinguished the regression.
The full suite then verified the restored versions. Mutation logs are temporary
`/tmp/brutus-mutant-<name>.log`; exact probes and results are recorded here durably.
Each Python probe used the following exact command prefix, followed by its target:

```bash
PYTHONPATH=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-isolation:$PWD BRUTUS_STATE_DIR=/Users/jfstudio/.local/share/forge-loop/20260916/remaining/brutus-test-state PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q <target>
```

- `backend_absence`: changed the unavailable-CLI result to `{ok: True, reply: ...}`;
  target `tests/test_backends.py::test_ask_claude_missing_cli` → **1 failed**.
- `provider_fallback`: inserted `complete(cfg, [])` before transport selection;
  target `tests/test_brain.py::test_selected_conversation_transport_never_changes_provider`
  → **4 failed**, `Expected 'complete' to not have been called` (alternate is mocked;
  no provider is launched).
- `unbacked_action`: disabled the unbacked-action predicate;
  target `tests/test_brain.py::test_each_transport_reaches_truthfulness_gates -k unbacked`
  → **2 failed**, returned `Queued it...` instead of the truthful refusal.
- `voice_owner`: reversed the owner guard to reject verified voice;
  target `tests/test_conversation.py::test_a_verified_voice_still_settles_a_pending_write`
  → **1 failed**, denial instead of the expected executed result.
- `typed_confirmation`: changed the guard to reject absent voice identity;
  target `tests/test_conversation.py::test_typing_is_never_asked_to_prove_a_voice`
  → **1 failed**, denial instead of the expected executed result.
- `service_running`: forced `running=True` for every loaded service;
  target `tests/test_process_control.py::test_the_services_endpoint_reports_every_brutus_service`
  → **1 failed**, idle service incorrectly running.

The three persistent Node mutation controls run in pytest as
`test_client_contract_rejects_seeded_regression[providers|lifecycle|playback]`.
They mutate JS only in memory and require Node exit nonzero **and AssertionError**:
remove Codex labeling, substitute a generic lifecycle lecture for pending progress,
and force playback ownership false. All three were rejected. Normal counterparts
also passed, so an unusable harness cannot satisfy the negative checks.

## Supplied failure inventory

- `tests/test_backends.py::test_ask_claude_missing_cli`
- `tests/test_brain.py::test_a_plain_answer_lands_in_one_round`
- `tests/test_brain.py::test_the_brain_gets_the_whole_history`
- `tests/test_brain.py::test_accepting_the_brains_offer_executes_it_without_repeating_or_reasking`
- `tests/test_brain.py::test_accepted_offer_drops_an_amputated_follow_up_question`
- `tests/test_brain.py::test_voice_turn_gets_a_short_spoken_contract_after_the_cached_system_prompt`
- `tests/test_brain.py::test_a_tool_round_executes_and_answers_in_one_user_message`
- `tests/test_brain.py::test_propose_action_drafts_and_never_executes`
- `tests/test_brain.py::test_prose_cannot_claim_a_proposal_without_a_stored_artifact`
- `tests/test_brain.py::test_unbacked_proposal_claim_gets_one_chance_to_call_the_real_tool`
- `tests/test_brain.py::test_voice_does_not_spend_a_second_model_call_correcting_an_action_claim`
- `tests/test_brain.py::test_propose_action_refuses_non_gated_tools`
- `tests/test_brain.py::test_voice_cannot_propose_keyboard_only_tools`
- `tests/test_brain.py::test_an_invented_ticket_is_challenged_then_annotated`
- `tests/test_brain.py::test_claude_failure_never_falls_back_to_cursor`
- `tests/test_brain.py::test_voice_social_fallback_never_calls_cursor`
- `tests/test_brain.py::test_voice_brain_failure_keeps_work_status_grounded_without_external_fallback`
- `tests/test_brain.py::test_voice_brain_failure_answers_greeting_without_external_fallback`
- `tests/test_brain.py::test_voice_auth_failure_never_exposes_credential_implementation`
- `tests/test_brain.py::test_total_failure_says_so_instead_of_inventing`
- `tests/test_brain.py::test_the_round_cap_fails_honest`
- `tests/test_brain.py::test_not_found_is_challenged_until_agent_threads_have_been_searched`
- `tests/test_brain.py::test_not_found_stands_once_the_threads_really_were_searched`
- `tests/test_conversation.py::test_a_verified_voice_still_settles_a_pending_write`
- `tests/test_conversation.py::test_typing_is_never_asked_to_prove_a_voice`
- `tests/test_process_control.py::test_the_services_endpoint_reports_every_brutus_service`
- `tests/test_session_voice_client.py::test_voice_instructions_live_in_help_and_supervisor_names_providers`
- `tests/test_session_voice_client.py::test_supervisor_hides_generic_lifecycle_lectures`
- `tests/test_session_voice_client.py::test_livekit_claims_spoken_output_before_connecting`

The voice-contract test was renamed to
`test_voice_turn_gets_the_current_short_spoken_contract`; every other listed
node retains its name. Final JUnit counts by affected module: backends 12, brain
56, conversation 34, process control 22, session voice client 18.

## Independent operator verification and delivery preservation

The frozen Forge run `723a329f66594890bca91a3a9a14cf20` remains blocked;
its receipt above is preserved. Independent operator execution of source
`487b4b385592121730c7a189cc3826a11d27405c` in the authorized private test
state directory passed **1037 tests, zero failures, zero skips**. The network
and provider/process isolation guard remained enabled.

Delivery integration preserves two measured differences in the installed
runtime: the 45-word voice contract and refusal of ambiguous repository
mutations from the Projects root. The installed example configuration's
explicit CLI/API controls and voice agent field are also preserved. Tests now
enforce these existing behaviors. The complete integration suite passed
**1037 tests, zero failures, zero skips**; workflow lint and shell/plist checks
passed. Evidence is retained in Studio's
`~/.local/share/forge-loop/20260916/remaining/brutus-preservation-final-suite.log`.
This verification does not assert real provider or voice conversation UAT.
