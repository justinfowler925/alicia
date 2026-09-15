import Foundation
import Gate0Core

/// Lightweight Gate 0 assertions that run without XCTest / Xcode.
@main
enum Gate0Smoke {
    static func main() {
        var failures = 0
        func check(_ name: String, _ ok: () -> Bool) {
            if ok() {
                print("PASS \(name)")
            } else {
                print("FAIL \(name)")
                failures += 1
            }
        }

        let engine = TurnBoundaryEngine(config: TurnBoundaryConfig(quietAfterChange: 0.5))
        let now = Date()
        check("marked text blocks") {
            engine.decide(
                snapshot: InputSnapshot(
                    text: "hello there",
                    markedTextActive: true,
                    focused: true,
                    lastChangeAt: now.addingTimeInterval(-2)
                ),
                now: now,
                appPaused: false,
                speaking: false,
                spokenText: ""
            ) == .wait(reason: "marked_text_active")
        }
        check("quiet commit") {
            engine.decide(
                snapshot: InputSnapshot(
                    text: "ship gate zero",
                    markedTextActive: false,
                    focused: true,
                    lastChangeAt: now.addingTimeInterval(-1)
                ),
                now: now,
                appPaused: false,
                speaking: false,
                spokenText: ""
            ) == .commit(text: "ship gate zero", reason: "quiet_after_unmarked_change")
        }
        check("dictation callback reason") {
            engine.decide(
                snapshot: InputSnapshot(
                    text: "done talking",
                    markedTextActive: false,
                    focused: true,
                    lastChangeAt: now.addingTimeInterval(-1),
                    dictationCallbackFired: true
                ),
                now: now,
                appPaused: false,
                speaking: false,
                spokenText: ""
            ) == .commit(text: "done talking", reason: "dictation_callback_plus_quiet")
        }
        check("interrupt speech") {
            engine.decide(
                snapshot: InputSnapshot(
                    text: "stop",
                    markedTextActive: false,
                    focused: true,
                    lastChangeAt: now,
                    changedDuringSpeech: true
                ),
                now: now,
                appPaused: false,
                speaking: true,
                spokenText: "Turn 1. Heard: something else"
            ) == .interruptSpeech(reason: "text_during_playback")
        }
        let spoken = "Turn 1. Heard: hello"
        check("echo hold") {
            engine.decide(
                snapshot: InputSnapshot(
                    text: spoken,
                    markedTextActive: false,
                    focused: true,
                    lastChangeAt: now,
                    changedDuringSpeech: true
                ),
                now: now,
                appPaused: false,
                speaking: true,
                spokenText: spoken
            ) == .wait(reason: "likely_echo_of_speech")
        }

        let log = EventLog()
        let speech = NullSpeechPlayback()
        let session = ProofSession(
            log: log,
            speech: speech,
            boundary: TurnBoundaryEngine(config: TurnBoundaryConfig(quietAfterChange: 0.5)),
            installsTimer: false
        )
        var cleared = 0
        session.onClearDraft = { cleared += 1 }
        session.updateDraft("first turn please", markedTextActive: false, focused: true)
        session.evaluate(now: Date().addingTimeInterval(2))
        check("session auto-commit") { session.turns.count == 1 }
        check("session clears draft") { session.snapshot.text.isEmpty && cleared == 1 }
        check("session speaks reply") { speech.spoken.count == 1 }

        let paused = ProofSession(
            log: EventLog(),
            speech: NullSpeechPlayback(),
            boundary: TurnBoundaryEngine(config: TurnBoundaryConfig(quietAfterChange: 0.1)),
            installsTimer: false
        )
        paused.setAppPaused(true)
        paused.updateDraft("should not commit", markedTextActive: false, focused: true)
        paused.evaluate(now: Date().addingTimeInterval(5))
        check("pause blocks commit") { paused.turns.isEmpty }

        if failures > 0 {
            fputs("\(failures) failure(s)\n", stderr)
            exit(1)
        }
        print("All Gate0Smoke checks passed.")
    }
}
