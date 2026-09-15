# Gate 0 go / no-go

Date: __________  
Mac hardware/OS/locale: __________  
iPhone hardware/OS/locale: __________  
Audio route(s): speakers / headphones / AirPods: __________  
Build: `Gate0Mac` / iOS app SHA or `swift build` time: __________  
Event log path(s): __________

## Mac sample (20 turns)

| Check | Pass? | Notes |
|---|---|---|
| 20 distinct automatic commits (no required per-turn send) | | |
| 5 deliberate long pauses handled acceptably | | |
| 5 corrections/revisions did not false-commit mid-edit | | |
| 5 interruptions cancelled speech and continued | | |
| Every turn got an audible Apple-voice reply | | |
| Pause / Resume / Stop speaking worked by name | | |
| Draft focus stayed stable during speech | | |
| App did not request microphone permission | | |
| Which OS signals actually fired? (draft_change / marked / dictation_callback / …) | | |

Mac result: **GO / NO-GO / BLOCKED**  
If NO-GO or BLOCKED, exact failing callback or interaction: __________

## iPhone sample (20 turns)

Same table. If Xcode missing, mark **BLOCKED — no iOS SDK / Xcode**.

iPhone result: **GO / NO-GO / BLOCKED**  
Failing detail: __________

## Background / non-owner speech (compatibility note)

Owner-voiceprint is **not** claimed by this gate. Record whether background speech produced unintended commits:

- Attempts: ____  
- Unintended commits/actions: ____  

## Decision

Overall Gate 0: **GO / NO-GO / BLOCKED**

Next work if not GO: resolve the specific compatibility failure above — do **not** patch LiveKit/ElevenLabs/browser mic as a substitute.
