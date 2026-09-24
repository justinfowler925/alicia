# Gate 0 — Voice Control proof

Status: **software ready on Mac; physical Voice Control sample not yet accepted.**
iPhone build blocked: this machine has Command Line Tools only (no full Xcode / iOS SDK).

## What this is

Minimal native proof required by [APPLE_ACCESSIBILITY_RESET_PLAN.md](../../docs/APPLE_ACCESSIBILITY_RESET_PLAN.md):

- Accessible draft editor named **Alicia draft** (no app microphone capture)
- Instrumented text / marked-text / focus / (iOS) dictation-end callbacks
- Automatic turn candidate (quiet after unmarked change; prefers dictation callback when present)
- `AVSpeechSynthesizer` replies with speakable **Pause**, **Resume**, **Stop speaking**
- JSONL event log under `~/.alicia/gate0-logs/` (iOS: app Documents/gate0-logs)

This gate does **not** call the Alicia brain. Replies are local (`Turn N. Heard: …`).

## Mac — run now

```bash
cd ~/Projects/alicia/native/Gate0Proof
./scripts/run-mac.sh
# or:
swift build --product Gate0Mac --build-system native
./.build/debug/Gate0Mac
```

Smoke (no GUI):

```bash
swift build --product Gate0Smoke --build-system native && ./.build/debug/Gate0Smoke
```

### Acceptance sample (required for go)

On this Mac, with Voice Control enabled:

1. Focus **Alicia draft**.
2. Complete **20 consecutive turns** without a per-turn send command (do not rely on **Force commit turn** for the sample).
3. Include **5 long pauses**, **5 corrections/revisions**, **5 interruptions** while speaking.
4. Confirm audible reply each turn; Pause/Resume/Stop work; focus stays on the draft.
5. Save the JSONL from `~/.alicia/gate0-logs/` as evidence.

Fill [GATE0_GO_NOGO.md](GATE0_GO_NOGO.md).

## iPhone — blocked until Xcode

Sources live in `Sources/Gate0iOS/`. Generate the Xcode project after installing full Xcode:

```bash
brew install xcodegen   # already available if used once
cd ~/Projects/alicia/native/Gate0Proof
xcodegen generate
open Gate0Proof.xcodeproj
```

Sign for your team, run on a physical iPhone, repeat the same 20-turn sample. Do not claim Gate 0 passed on Simulator alone.

Exact blocker recorded 2026-09-14: `xcodebuild` requires Xcode; active developer directory is Command Line Tools; no `/Applications/Xcode.app`.

## Intentionally out of scope

- Retiring LiveKit / ElevenLabs / Ear
- Durable conversation protocol (Gate 1)
- Studio service move (Gate 2)
- Re-enabling Atlas or Anthropic API kill switches
