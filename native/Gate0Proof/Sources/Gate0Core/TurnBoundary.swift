import Foundation

/// Signals the OS and text control actually expose. Gate 0 must not invent a
/// microphone stream — only react to text/composition/focus/dictation callbacks.
public struct InputSnapshot: Equatable, Sendable {
    public var text: String
    public var markedTextActive: Bool
    public var focused: Bool
    public var lastChangeAt: Date
    public var dictationCallbackFired: Bool
    /// True when the latest change arrived while speech was playing.
    public var changedDuringSpeech: Bool

    public init(
        text: String = "",
        markedTextActive: Bool = false,
        focused: Bool = false,
        lastChangeAt: Date = .distantPast,
        dictationCallbackFired: Bool = false,
        changedDuringSpeech: Bool = false
    ) {
        self.text = text
        self.markedTextActive = markedTextActive
        self.focused = focused
        self.lastChangeAt = lastChangeAt
        self.dictationCallbackFired = dictationCallbackFired
        self.changedDuringSpeech = changedDuringSpeech
    }

    public var trimmed: String {
        text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

public struct TurnBoundaryConfig: Equatable, Sendable {
    /// Quiet time after the last unmarked text change before a commit candidate.
    /// This is an instrumented candidate, not proven Voice Control end-of-utterance.
    public var quietAfterChange: TimeInterval
    /// Minimum characters before auto-commit is allowed.
    public var minimumCharacters: Int
    /// If text arrives during playback, cancel speech and treat as interruption
    /// once this many new characters accumulate (echo filter soft gate).
    public var interruptionCharacters: Int

    public init(
        quietAfterChange: TimeInterval = 1.4,
        minimumCharacters: Int = 2,
        interruptionCharacters: Int = 3
    ) {
        self.quietAfterChange = quietAfterChange
        self.minimumCharacters = minimumCharacters
        self.interruptionCharacters = interruptionCharacters
    }
}

public enum TurnDecision: Equatable, Sendable {
    case wait(reason: String)
    case interruptSpeech(reason: String)
    case commit(text: String, reason: String)
}

/// App-local automatic turn candidate.
///
/// Design notes (from APPLE_ACCESSIBILITY_RESET_PLAN.md):
/// - Mere text stability is not proof the user finished speaking.
/// - A global three-second timer already caused mid-thought sends.
/// - This candidate resets on every change, refuses while marked text is active,
///   and separates interruption from commit.
/// Physical Voice Control samples decide whether it is acceptable.
public struct TurnBoundaryEngine: Sendable {
    public var config: TurnBoundaryConfig

    public init(config: TurnBoundaryConfig = TurnBoundaryConfig()) {
        self.config = config
    }

    public func decide(
        snapshot: InputSnapshot,
        now: Date,
        appPaused: Bool,
        speaking: Bool,
        spokenText: String
    ) -> TurnDecision {
        if appPaused {
            return .wait(reason: "app_paused")
        }
        if !snapshot.focused {
            return .wait(reason: "editor_unfocused")
        }
        if snapshot.markedTextActive {
            return .wait(reason: "marked_text_active")
        }
        let text = snapshot.trimmed
        if text.isEmpty {
            return .wait(reason: "empty")
        }
        if text.count < config.minimumCharacters {
            return .wait(reason: "below_minimum")
        }

        if speaking {
            // Echo of our own speech often reappears as near-identical draft text.
            // Real interruption adds/changes content beyond the spoken reply.
            let normalizedDraft = Self.normalize(text)
            let normalizedSpoken = Self.normalize(spokenText)
            if !normalizedSpoken.isEmpty, normalizedDraft == normalizedSpoken || normalizedSpoken.hasPrefix(normalizedDraft) {
                return .wait(reason: "likely_echo_of_speech")
            }
            if snapshot.changedDuringSpeech, text.count >= config.interruptionCharacters {
                return .interruptSpeech(reason: "text_during_playback")
            }
            return .wait(reason: "speaking_hold")
        }

        let quiet = now.timeIntervalSince(snapshot.lastChangeAt)
        if quiet < config.quietAfterChange {
            return .wait(reason: "quiet_window")
        }

        // Prefer committing after an iOS dictation-end callback when it fires.
        if snapshot.dictationCallbackFired {
            return .commit(text: text, reason: "dictation_callback_plus_quiet")
        }
        return .commit(text: text, reason: "quiet_after_unmarked_change")
    }

    private static func normalize(_ value: String) -> String {
        value
            .lowercased()
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }
}
