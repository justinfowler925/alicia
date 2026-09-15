# Brutus Apple accessibility reset

Status: authoritative replacement direction and implementation plan; **Gate 0 Mac proof software landed** under `native/Gate0Proof/` (2026-09-14). Physical Voice Control acceptance samples and iPhone build (needs full Xcode) are still open — not product-accepted.
Decision date: September 7, 2026.

## The decision

Rebuild Brutus's conversation surface around **Apple accessibility Voice Control for speech input and native Apple speech output**. Preserve the useful work service and existing data. Replace the competing voice transports and session plumbing as a coherent release.

The intended experience is continuous back-and-forth conversation after opening Brutus, with automatic turn-taking, interruption, pause/resume, and continuity when returning later or using another supported Apple device. Siri is explicitly excluded. A shortcut or spoken send command required for every turn does not satisfy the requirement. Push-to-talk does not satisfy it either.

This document is the single implementation plan for that reset. Older plans and evaluation reports are historical evidence. They cannot override these requirements or establish current acceptance. This plan changes direction; it does not claim the existing production runtime has changed.

## Why reset the surface

The audited release, `bc3ea6792f834d14b4dfcbf4490e3a0a695bc36c`, combines several different products:

- `/session`: browser microphone, LiveKit transport, ElevenLabs Scribe recognition, Silero turn detection, SpeechBrain speaker verification, the conversation service, then ElevenLabs speech output. A browser SpeechRecognition fallback also remains in this client.
- `/mobile`: a separate browser SpeechRecognition implementation and its own resume keys. It does not use the same LiveKit owner gate as `/session`.
- `voice.py` and `ear.py`: another local microphone/Whisper path. The Ear is disabled in the inspected runtime; installed/importable Whisper is not evidence that Whisper is the active session recognizer.
- `/api/session/*`: persisted conversation turns and native Claude tool use. `/api/chat`, CLI and some MCP paths still use the older chat resolver and a different memory contract.
- Health/profile metadata describes a Cursor conversation profile while the actual session brain calls Claude. This is reporting/configuration drift, not evidence of an intentional per-turn switch.

These are different entry paths with different ownership and failure behavior, not interchangeable skins over one tested voice system. Fixing one path has repeatedly left another path or an old plan authoritative elsewhere.

The existing delivery policy contains tests, lint, Git, deployment and production HTTP readback. It omits continuous spoken interaction, cross-device continuation, output playback, and background-speaker behavior. Code passing that policy cannot establish this product is complete.

## Apple capability boundary: verify before building the replacement

Apple documents Voice Control as an accessibility interface that dictates into text controls and activates named UI actions. It is not documented as an app-owned continuous transcription stream. Apple also documents a separate Speech framework; using that framework is **not automatically the same as using accessibility Voice Control**.

Therefore the first implementation deliverable is a small native interaction proof, not a full rewrite:

1. An accessible native text editor receives real Voice Control dictation. The Brutus app does not request or capture microphone audio. Use native text controls and public accessibility labels/actions, not global AX scraping, clipboard polling, private preferences, or simulated clicks in other apps.
2. Instrument text replacement, composition/marked-text state, focus and documented dictation callbacks. On iOS, `UITextInput` offers dictation callbacks, but Apple distinguishes Voice Control from ordinary Dictation. Verify which callbacks actually fire with Voice Control; do not assume a phrase-final event exists on all platforms.
3. Establish automatic turn boundaries from actual available input signals. Mere text stability is not proof that the user finished speaking. A global three-second timer already caused mid-thought sends in the historical system. A bounded app-local candidate must pass long-pause, correction, partial-result and echo tests before it can be selected.
4. Speak a controlled reply through `AVSpeechSynthesizer` using an installed Apple voice. Verify speech start/finish/cancel, Voice Control coexistence, audible playback and interruption. Keep the text editor's focus stable and expose named Pause, Resume and Stop speaking controls.
5. Test with speaker output as well as headphones. Incoming text during playback must not be assumed to be the user; output can be re-dictated. Suppressing all input during playback would break interruption and does not pass.

**Go/no-go:** prove automatic turn-taking and interruption with Voice Control on a real Mac and a real iPhone before building the broader client or retiring production voice. If the public interface cannot support those behaviors, record the exact failing callback/interaction and mark the compatibility gate blocked. Do not quietly replace Voice Control with SpeechAnalyzer, Siri, Web Speech, or a required per-turn command. A different input mechanism requires a new explicit product decision.

The older owner-only speaker requirement remains a separate compatibility gate. Voice Control recognizing words, a Personal Voice output voice, and an authenticated device are not proof of who spoke. No public owner-voiceprint verification interface was established in this audit. Preserve the requirement to reject unintended/background commands in the acceptance suite; do not claim native device authentication replaces that behavior. If strict biometric speaker filtering cannot be met with the selected input, resolve that policy explicitly before cutover. Do not add another raw-audio listener to disguise the conflict.

## Target architecture

### One native client family

Use a shared Swift client/session package with native macOS and iOS/iPadOS shells. Use AppKit/UIKit text controls where required to receive and inspect actual dictation behavior; SwiftUI can own the surrounding interface. Do not put another web microphone app inside a native wrapper.

The client owns the accessible editor, draft revision, turn boundary adapter, local Apple speech playback, visible listening/pause/reconnect state and device credentials. It sends text and structured commands to Brutus. It never uploads microphone audio, generates provider credentials, or maintains a second work ledger.

Voice Control is the OS input owner. Brutus's Pause stops accepting/submitting input for its conversation and stops queued playback; it must not claim it disabled the OS microphone. System Voice Control sleep/wake remains controlled by the OS. Test both app pause and system pause independently.

Use Apple's installed voices through `AVSpeechSynthesizer`, with one saved voice preference and an explicit per-device availability check. Do not promise an identical voice identifier exists on every device. Personal Voice is optional, requires Apple's authorization, and is an output feature, not speaker authentication. Ensure OS screen-reader speech and app speech do not create duplicate playback.

### One conversation service

Retain the tested native-tool conversation brain and the useful Canon/tool adapters. Consolidate every Brutus entry surface onto a versioned conversation API. Keep one explicitly configured conversation model; retain the current native Claude loop initially so the reset does not also become an unmeasured model migration. Derive health and UI provider names from the actual running path.

Give each authenticated owner a stable conversation list and explicit current conversation pointer. A device, browser tab, CLI invocation, MCP invocation, and network connection are not conversation identities. Opening another client resumes the selected server conversation rather than creating a blank one or pasting a summary as a new user message.

Define and test these protocol elements before implementing clients:

- Stable `conversation_id`, authenticated `device_id`, client-generated `request_id`, `draft_revision`, and monotonic server `event_seq`.
- Persist the accepted request and its unique identity before acknowledging it. Repeated delivery of the same request returns the same result. Conflicting reuse of an ID fails visibly.
- Durable turn states: accepted, running, completed, interrupted, failed. Server-side cancellation and proposal handling share that lifecycle; cancelling an HTTP request alone does not prove execution stopped.
- Durable event log and replay cursor. Reconnect retrieves missed events plus a snapshot. A completed old answer is not automatically spoken again.
- A conversation-scoped active speech device lease prevents two devices from answering aloud or submitting the same ongoing draft. Other devices can read. Handoff transfers the lease and sequence position explicitly.
- Local draft/outbox storage survives app termination and offline intervals. Old pending input is shown and resumed deliberately; reconnect must not silently execute a stale consequential request.
- Context assembly combines the conversation, a source-linked summary for longer histories, relevant durable memory and fresh work-state tools. The current last-40-turn window alone is insufficient for long-term continuity.

Reuse the existing stores initially with explicit migrations, constraints and online backups. Do not put live SQLite files in iCloud Drive or open the same database from multiple machines. iCloud can carry a Handoff reference or preference; the service remains the authority for conversation and work state.

### One durable service home

A phone must reach the same service while the laptop is shut. The present loopback-only laptop daemon cannot meet that requirement.

Preferred deployment target: a dedicated Brutus service on the existing always-on Mac Studio, independent of Atlas. The audit established an online Studio peer, not its readiness for this migration. Inventory its ownership, power/network behavior, disk/backups and service dependencies before selecting the exact host. Keep local agent-session observers on their native machines; they publish bounded facts to the service rather than copying whole private transcripts or requiring the Studio to read nonexistent laptop paths.

Use authenticated TLS over the existing private network for device access. Brutus's current conversation, enrollment and session-artifact routes assume loopback and lack the owner dependency used by the Canon router. Never make those old routes remotely reachable unchanged. New conversation reads, writes, events, cancellation and approvals all need device/owner authorization, revocation and scoped credentials. Store device credentials in Keychain; provider secrets stay in the service credential profile.

Keep the Canon evidence/approval model, Linear source authority, Zoom ingestion and relevant work tools. Observe deployed state, work state and model/provider state separately. No dashboard flag can certify a successful spoken reply.

## Platform contract

Mac, iPhone and iPad are the primary Voice Control targets. A release claim requires a recorded hardware/OS/locale matrix and physical-device tests for each claimed platform. The audited Mac runs macOS 26.6.2; other device versions and native build/signing readiness were not established. Command Line Tools are installed; a full selected Xcode installation was not found.

Apple documents Voice Control on Vision Pro; add it only after its client and device interaction tests pass. AirPods are an input/output route through a supported host, not an independent Brutus client. Apple Watch's documented Voice Control route is via iPhone mirroring; this is not a verified standalone continuous Watch experience. No independent HomePod, Apple TV or CarPlay accessibility conversation path was established here. Do not label the release “every Apple device” or silently introduce Siri to fill those gaps.

Within a device, app switching must preserve conversation state and drafts. Voice Control dictates into the focused surface; Brutus must not capture text intended for another app or steal focus back. Continuity with other agent products requires an explicit adapter/reference, not an assumption that their private task stores become the Brutus transcript.

Lock, sleep, foreground/background transitions, phone/audio interruptions and output-route changes must have tested outcomes. A visible paused state with reliable resume is honest; claiming uninterrupted background listening without proof is not. Record any resulting mismatch with the desired usage before accepting the platform.

## Retain, replace, retire

### Retain and harden

- `canon/`, `workflow_control.py`, `workflow_http.py`: work, evidence and review primitives. Add actual task bindings and acceptance visibility; preserve owner authority.
- `brain.py` native Claude tool loop and relevant `tools.py` adapters: cognition and grounded actions. Consolidate legacy entry points and remove stale provider claims.
- `session.py`, `memory.py`, `todos.py`: preserve existing data and useful semantics; migrate rather than wipe. Add durable protocol, conversation identity and history compaction.
- `zoom_*`, GitHub evidence ingestion and backups: preserve working intake. Back up all retained state, not only Canon.
- Credential delivery and commit-versioned deployment: retain the mechanisms that prevent runtime drift. Simplify them after the audio workers are retired.
- Agent observers and supervisor: retain useful source adapters; bind active work explicitly and distinguish a native task's lifecycle from verified product acceptance.

### Replace as one boundary

- The voice/conversation portions of `static/session.js` and `static/mobile.js`: replace with native clients sharing one protocol. Keep the old web work tools during transition.
- `session_bus.py` as the sole delivery mechanism: replace its volatile delivery assumptions with a durable event log/replay. A local fan-out helper can remain on top.
- Browser sessionStorage resume conventions, legacy `memory.conversations` last-pair chat behavior, and `/api/chat`/CLI/MCP divergence: migrate clients to the same canonical conversation API.
- Blanket delivery policy: add device voice, native accessibility, continuity, playback, negative controls and restored-state checks. Separate report delivery from product release requirements.

### Retire after replacement acceptance

- `livekit_agent.py`, the LiveKit server and worker launchd jobs, their scripts, room tokens, browser SDK import and media transport.
- ElevenLabs STT and TTS from Brutus, `/api/speak` audio-proxy behavior, browser SpeechRecognition fallback, Whisper/PyAudio/pynput Ear path, and raw-audio enrollment UI.
- SpeechBrain/Torch/torchaudio speaker machinery, after the owner/background-speech compatibility gate is explicitly resolved. Never claim deleting this subsystem preserves biometric speaker verification automatically.
- Brutus voice dependencies and credential fields that no retained consumer uses. Remove imports, routes, tests, installers and health fields together; “disabled” must not remain the permanent architecture.
- Local-LLM/Atlas voice/factory compatibility paths after call-site and external-consumer checks. `client.py` still participates in retained code; do not delete it wholesale because of its historical name.
- The external `TalkToBert.app`/`~/.brutus/voice/bert_listen.py` bridge after checking command consumers. Its accessibility command launches a recorder/transcriber; it is not a native Voice Control transcript bridge. Preserve working general Voice Control commands such as Cmd+Return.

Avatar, Demo Maker, sites and other unrelated tools are outside the conversation reset. Keep or separately extract them after identifying consumers. Do not delete projects or their data to make the repository look smaller. Preserve rescue branches as historical material; the existing orphan audit found no reason to merge its five files unchanged.

## Delivery sequence and acceptance

### Gate 0 — settle the architecture boundary

Deliver the minimal Mac and iPhone Voice Control proof described above. This is the first build task and the go/no-go point. Report which input events the OS actually supplies, how turns end, how output is interrupted, and whether background speech can trigger actions. No production cutover, broad UI build or fallback recognizer precedes this result.

**Implementation (2026-09-14):** shared core + Mac AppKit app + iOS UIKit sources live in [`native/Gate0Proof/`](../native/Gate0Proof/). Mac binary builds with Command Line Tools (`./scripts/run-mac.sh`). Smoke checks: `Gate0Smoke`. iPhone Xcode project via `xcodegen generate` after installing full Xcode — currently blocked on this machine (CLT only). Acceptance form: [`GATE0_GO_NOGO.md`](../native/Gate0Proof/GATE0_GO_NOGO.md). Software ≠ Gate 0 pass.

Required initial sample on each of those two devices: 20 consecutive turns, including five deliberately long pauses, five corrections/revisions and five interruptions. All turns must be distinct, complete and replied to audibly without a required per-turn send action. The sample is a feasibility gate, not the final reliability claim. If it fails, the next work is resolving the specific compatibility failure, not another voice stack patch.

### Gate 1 — make conversation state durable

Implement and test the versioned protocol against isolated copies of state. Import existing sessions, turns, notes, tasks and pending proposals with stable mappings; record counts and canonical content digests before/after. Test 100 duplicate request deliveries, crash after acceptance/before reply, cancelled generations, disconnected event streams and reconnect replay. Each accepted request has one durable identity; no consequential tool execution is repeated.

Persist critical state before reporting success. Add bounded history compaction with source turn references and a test where a fact older than 40 turns is correctly recovered without treating stale work status as current truth. Recover pending work and proposals after restart.

### Gate 2 — build the shared native experience

Build Mac and iPhone/iPad shells from the proven adapter, with stable named controls and one playback owner. Include text review, accessible status, explicit current conversation and unfinished-work state, native speech voice settings, and connection recovery. Native accessibility behavior owns the interaction; visual polish cannot replace it.

Integrate the private service endpoint and local-machine observers. Preflight the Studio, migrate a verified state snapshot with old writer quiesced, and test phone access while the laptop is shut. A local-only prototype does not pass cross-device delivery.

### Gate 3 — accept the product and remove the old boundary

Before cutover, complete all of the following on the exact candidate artifact:

- 10 real conversation sessions of at least 10 turns on each supported launch platform: 100 turns/platform, with hardware, OS, locale and audio route recorded. Include speakers and AirPods/headphones, quiet and ordinary background conditions, short commands and long thoughts.
- At least 20 controlled background/non-owner speech attempts per platform and 20 playback-echo attempts: zero unintended submissions or actions under the agreed owner/background-speech contract. Device authentication alone is not this test.
- At least 20 pause/resume and interruption trials per platform: no queued stale reply after pause; no lost resumed draft; no duplicated speech. Proposed local stop target: at most 300 ms from the app receiving the stop action, plus separately measured Voice Control recognition delay.
- Ten Mac → iPhone → Mac handoffs and ten app close/reopen cycles: same conversation, pending proposal, context, draft disposition and work state; one speech owner; no re-spoken historical reply. Repeat with the laptop shut after the service moves.
- Force service restart during five running requests and replay 100 requests: acknowledged input survives, output status is honest, and side effects are not duplicated. Disconnect network and change audio routes during interaction.
- Measure end-of-user-speech to first audible reply on-device. Proposed initial simple-turn targets: p50 at most 3 seconds, p95 at most 6 seconds, after model warmup. Tool-heavy turns are a separate population and get accurate in-progress state. If the Voice Control boundary cannot expose speech-end timing, use an independent timed observation; do not relabel text-change-to-reply as speech latency.
- Measure cold/warm startup, app CPU/memory and service/model timing. Reuse model connections, cache stable prompt/tool prefixes, bound history/tool results, and stream speakable reply segments only after correctness and cancellation pass. No brain downgrades or meaning-changing truncation to manufacture a fast number.
- Restore the complete state bundle on a clean target. Proposed operations targets: acknowledged turns survive process restart, backup recovery point at most 15 minutes, service recovery at most 30 minutes. Prove the targets; do not infer them from file existence.

Then perform one coordinated cutover: signed/versioned clients, service version and schema readback, exact acceptance receipts, old voice jobs stopped, old endpoints disabled, obsolete packages and credentials removed from the active profile, and a clean restart that cannot resurrect LiveKit or Ear. Run negative probes confirming the retired paths are unavailable. Observe normal use for seven days before deleting rollback material.

Rollback preserves data. Keep the pre-cutover artifact and verified online backups. Fence writers and check schema compatibility before switching service versions. Prefer restoring the previous executable against compatible current data; if reverse migration is required, export/reconcile new events first. Never restore an old database over accepted new work just to make rollback easy. A rollback to old voice is an explicitly degraded mode, not a successful reset.

## Plan reconciliation

- `README.md` and `BRUTUS.md`: this document controls the new direction; their older provider/Atlas/voice claims are not current acceptance.
- `CONVERSATION_REBUILD_PLAN.md`: preserve the native-tool brain/gated-action lessons; supersede browser/Whisper/Ear voice architecture and “shipped” as overall product acceptance.
- `NUCLEUS_COMMAND_CENTER.md`: retain work graph/read tools; its queue-first default is not the new conversation-first home.
- `SESSION_UI_SHINE_BUILD_PLAN.md`, `SESSION_IDEAS_BUILD_PLAN.md`, `SESSION_IDEAS_WAVE2_BUILD_PLAN.md`, `FOCUS_SURFACE_BUILD_PLAN.md`, `UI_BUILD_PLAN.md`, `UI_UX_AUDIT.md`: historical interface specifications; retain useful note/review behaviors, replace old voice and resume contracts.
- `PIPELINE_UNFUCK.md` and historical Atlas operator plans: historical recovery records, not the architecture to rebuild.
- `REMEDIATION_EVAL_RESULTS.md` and `VOICE_FOLLOW_THROUGH_EVAL_RESULTS.md`: dated scoped evidence. They do not establish native accessibility voice, multi-device delivery or current acceptance.
- Fowler Brain's September 7 speech recovery/reconciliation records: preserve the failure evidence. Their patch-oriented continuation is superseded by Gate 0 and this reset; unresolved owner/background-speech requirements stay explicit.
- Linear REV-490 is marked Done while later voice acceptance failed; REV-583 is Backlog while speaker code is deployed. Reconcile these with the existing Canon implementation/verification items and this reset item. Do not create parallel rewrite queues or close acceptance from a deployment event.

## Ownership and scope control

One implementation lead owns the native adapter, protocol boundary and release evidence end to end. Platform-specific tasks may contribute to that same contract after Gate 0. Canon records the active task, repository, evidence and acceptance state; Linear remains source authority for its issues. Fowler Brain holds the durable user requirement and a pointer here, not another copy of this plan.

This audit authorizes and delivers the plan. No service cutover, legacy deletion, new device permission or model/provider migration was performed. Product release remains incomplete until the gates above pass. Avoid spending the first milestone polishing a dashboard, re-enabling Atlas, swapping LLMs, loosening a speaker threshold, or rebuilding components already retained.

## Primary Apple references

- [Voice Control developer overview](https://developer.apple.com/documentation/accessibility/voice-control): accessibility-driven UI interaction and text entry.
- [Voice Control on Mac](https://support.apple.com/en-au/guide/mac-help/mh40719/mac) and [Voice Control on iPhone](https://support.apple.com/guide/iphone/use-voice-control-iph2c21a3c88/ios): dictation, named controls and pause/resume; distinct from standard Dictation.
- [SwiftUI input labels](https://developer.apple.com/documentation/swiftui/view/accessibilityinputlabels(_:)-2upwq/): speakable control names.
- [UITextInput dictation callback](https://developer.apple.com/documentation/uikit/uitextinput/dictationrecordingdidend()): documented callback, not proof of Voice Control event parity.
- [Native Apple speech synthesis](https://developer.apple.com/documentation/avfaudio/avspeechsynthesizer): installed voices, speech lifecycle and playback control.
- [Speech framework](https://developer.apple.com/documentation/speech): a separate app speech-recognition API; not selected as a hidden replacement for Voice Control.
- [Vision Pro accessibility](https://support.apple.com/en-us/120052) and [Apple Watch Mirroring](https://support.apple.com/guide/watch/apple-watch-mirroring-apd890848603/26/watchos/26): platform-specific availability, not universal device parity.

Apple capability claims were checked September 7, 2026. The native bridge and physical-device acceptance remain unverified. That limitation is deliberately visible at the front of the implementation sequence.
