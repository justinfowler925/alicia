# Compact voice overview

Implements Justin's reviewed September 10 design: brief intent readback, compact
Claude/Cursor/OpenAI session summaries, and existing workspace tools below.

- The main surface shows six observed sessions, ordered by attention then live
  state. Show more reveals additional observed sessions. Coverage is explicit;
  unavailable sources and unverified progress are not represented as successful work.
- Expanding a session exposes next action, evidence, and Discuss with Brutus.
  This asks Brutus about that exact session; it does not silently send commands
  to another provider. Existing proposal gates still apply to agent handoffs.
- Archive from Brutus hides a session using the existing local archive overlay.
  Archived sessions exposes Restore. Neither action stops a process or deletes
  a provider conversation. Running → Cancel remains the explicit process stop.
  The same overlay filters background supervision and spoken status queries.
- Ordinary sessions receive structured judgments within the existing one-call
  sweep budget. Pending sessions are filled in on later sweeps. Failed provider
  attempts are cached until the source changes. Progress does not earn a spoken
  interruption merely because a summary was produced.
- The latest reply's first complete sentence supplies the readback. The brain
  is instructed to acknowledge intended outcome/scope for new instructions and
  corrections, while questions still get direct answers. This is a prompt
  behavior, not a guarantee of semantic understanding.
- Voice startup remains an explicit gesture, using existing audio transport,
  enrollment and permission behavior. Correct intent starts listening or
  interrupts speech; it does not synthesize a correction on the user's behalf.
- Conversation and keyboard are collapsed, with a bounded transcript scroller
  when opened. Projects & work expands in document flow, preserving all five
  existing workspace views and their table engine. Tables scroll horizontally
  inside their own container on narrow screens.
- Expanded workspace panels and Queue columns have no vertical height cap.
  Regression fixtures contain 30 projects and a long Queue column so checking
  section order alone cannot accidentally pass while a nested scroller remains.

## Verification

`scripts/verify-voice-overview.cjs` exercises the actual shipped assets with
isolated fixtures and no microphone/network mutations: 15 sessions, paging,
readback, restored history, stable expansion, five workspace tabs, mute, and
390/768/1280/1920px layouts. Run with an installed Playwright on NODE_PATH.
Python tests cover summary backfill, single-call budget, unchanged evidence,
silent ordinary progress, and the served stylesheet.

The source precedent is the approved preview and existing Brutus components.
The installed Shine catalog returned a record reference without a screenshot;
external reference comparison cannot establish proof for this design. Browser
workflow/layout tests and visual inspection supply direct product evidence.
Live microphone/speaker testing requires Justin's voice and is separate from
browser state and layout verification.
