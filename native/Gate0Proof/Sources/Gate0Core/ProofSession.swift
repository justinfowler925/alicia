import Foundation

public struct ProofTurn: Equatable, Sendable, Identifiable {
    public let id: UUID
    public let userText: String
    public let replyText: String
    public let commitReason: String
    public let at: Date

    public init(userText: String, replyText: String, commitReason: String, at: Date = Date()) {
        self.id = UUID()
        self.userText = userText
        self.replyText = replyText
        self.commitReason = commitReason
        self.at = at
    }
}

public protocol ProofClock: Sendable {
    func now() -> Date
}

public struct SystemProofClock: ProofClock {
    public init() {}
    public func now() -> Date { Date() }
}

/// Gate 0 session: automatic turn-taking over an accessible draft editor.
/// Reply is local and deterministic — this gate proves the voice boundary,
/// not the product brain.
public final class ProofSession: @unchecked Sendable {
    public let log: EventLog
    public let speech: SpeechPlaying
    public let boundary: TurnBoundaryEngine
    public private(set) var snapshot = InputSnapshot()
    public private(set) var appPaused = false
    public private(set) var turns: [ProofTurn] = []
    public private(set) var statusText = "Listening — Voice Control into Alicia draft. App does not use the microphone."

    public var onStatusChange: ((String) -> Void)?
    public var onTurnsChange: (([ProofTurn]) -> Void)?
    public var onClearDraft: (() -> Void)?

    private let clock: ProofClock
    private var timer: Timer?
    private let lock = NSLock()
    private let installsTimer: Bool

    public init(
        log: EventLog,
        speech: SpeechPlaying? = nil,
        boundary: TurnBoundaryEngine = TurnBoundaryEngine(),
        clock: ProofClock = SystemProofClock(),
        installsTimer: Bool = true
    ) {
        self.log = log
        self.speech = speech ?? SpeechPlayback(log: log)
        self.boundary = boundary
        self.clock = clock
        self.installsTimer = installsTimer
        self.speech.delegate = self
    }

    public func start() {
        log.append(Gate0Event(kind: "session_start", detail: "Gate0Proof"))
        publishStatus(statusText)
        guard installsTimer else { return }
        DispatchQueue.main.async { [weak self] in
            self?.installTimer()
        }
    }

    public func stop() {
        timer?.invalidate()
        timer = nil
        speech.stop()
        log.append(Gate0Event(kind: "session_stop"))
    }

    private func installTimer() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in
            self?.evaluate()
        }
    }

    public func setAppPaused(_ paused: Bool) {
        appPaused = paused
        if paused {
            speech.pause()
            publishStatus("Paused — app is not accepting turn commits. System Voice Control is unchanged.")
            log.append(Gate0Event(kind: "app_pause", detail: "", draft: snapshot.text, paused: true))
        } else {
            speech.resume()
            publishStatus("Listening — Voice Control into Alicia draft.")
            log.append(Gate0Event(kind: "app_resume", detail: "", draft: snapshot.text, paused: false))
        }
    }

    public func updateDraft(
        _ text: String,
        markedTextActive: Bool,
        focused: Bool,
        dictationCallbackFired: Bool = false
    ) {
        let speaking = speech.isSpeaking
        var next = snapshot
        let changed = next.text != text || next.markedTextActive != markedTextActive
        next.text = text
        next.markedTextActive = markedTextActive
        next.focused = focused
        if dictationCallbackFired {
            next.dictationCallbackFired = true
        }
        if changed {
            next.lastChangeAt = clock.now()
            next.changedDuringSpeech = speaking
            log.append(
                Gate0Event(
                    kind: "draft_change",
                    detail: markedTextActive ? "marked" : "committed_range",
                    draft: text,
                    speaking: speaking,
                    paused: appPaused,
                    markedTextActive: markedTextActive
                )
            )
        }
        snapshot = next
        evaluate()
    }

    public func noteFocus(_ focused: Bool) {
        snapshot.focused = focused
        log.append(
            Gate0Event(
                kind: focused ? "focus_gained" : "focus_lost",
                draft: snapshot.text,
                speaking: speech.isSpeaking,
                paused: appPaused,
                markedTextActive: snapshot.markedTextActive
            )
        )
    }

    public func forceCommit() {
        let text = snapshot.trimmed
        guard !text.isEmpty else { return }
        commit(text: text, reason: "force_commit_control")
    }

    public func evaluate(now: Date? = nil) {
        lock.lock(); defer { lock.unlock() }
        let decision = boundary.decide(
            snapshot: snapshot,
            now: now ?? clock.now(),
            appPaused: appPaused,
            speaking: speech.isSpeaking,
            spokenText: speech.currentUtterance
        )
        switch decision {
        case .wait:
            return
        case .interruptSpeech(let reason):
            log.append(Gate0Event(kind: "interrupt", detail: reason, draft: snapshot.text, speaking: true))
            speech.stop()
            publishStatus("Interrupted — keep talking; turn will commit after a quiet stretch.")
        case .commit(let text, let reason):
            commit(text: text, reason: reason)
        }
    }

    private func commit(text: String, reason: String) {
        // Clear draft first so quiet-window cannot re-commit the same text.
        snapshot.text = ""
        snapshot.lastChangeAt = clock.now()
        snapshot.dictationCallbackFired = false
        snapshot.changedDuringSpeech = false
        onClearDraft?()

        let reply = Self.localReply(for: text, turnNumber: turns.count + 1)
        let turn = ProofTurn(userText: text, replyText: reply, commitReason: reason, at: clock.now())
        turns.append(turn)
        onTurnsChange?(turns)
        log.append(
            Gate0Event(
                kind: "turn_commit",
                detail: "\(reason) | \(text)",
                draft: "",
                speaking: false,
                paused: appPaused
            )
        )
        publishStatus("Turn \(turns.count): \(text)")
        speech.speak(reply)
    }

    private func publishStatus(_ text: String) {
        statusText = text
        onStatusChange?(text)
    }

    /// Deterministic audible reply for Gate 0 boundary proof.
    public static func localReply(for userText: String, turnNumber: Int) -> String {
        let clipped = userText.count > 120 ? String(userText.prefix(117)) + "..." : userText
        return "Turn \(turnNumber). Heard: \(clipped)"
    }
}

extension ProofSession: SpeechPlaybackDelegate {
    public func speechDidStart(utterance: String) {
        publishStatus("Speaking — say something to interrupt, or use Stop speaking.")
    }

    public func speechDidFinish(utterance: String, cancelled: Bool) {
        if appPaused {
            publishStatus("Paused.")
        } else {
            publishStatus("Listening — Voice Control into Alicia draft.")
        }
    }
}
