import AVFoundation
import Foundation

public protocol SpeechPlaybackDelegate: AnyObject {
    func speechDidStart(utterance: String)
    func speechDidFinish(utterance: String, cancelled: Bool)
}

public protocol SpeechPlaying: AnyObject {
    var isSpeaking: Bool { get }
    var isPausedByApp: Bool { get }
    var currentUtterance: String { get }
    var delegate: SpeechPlaybackDelegate? { get set }
    func speak(_ text: String)
    func pause()
    func resume()
    func stop()
}

/// Silent stand-in for unit tests — never touches AVSpeechSynthesizer.
public final class NullSpeechPlayback: SpeechPlaying {
    public weak var delegate: SpeechPlaybackDelegate?
    public private(set) var isSpeaking = false
    public private(set) var isPausedByApp = false
    public private(set) var currentUtterance = ""
    public private(set) var spoken: [String] = []

    public init() {}

    public func speak(_ text: String) {
        currentUtterance = text
        isSpeaking = true
        spoken.append(text)
        delegate?.speechDidStart(utterance: text)
        isSpeaking = false
        currentUtterance = ""
        delegate?.speechDidFinish(utterance: text, cancelled: false)
    }

    public func pause() { isPausedByApp = true }
    public func resume() { isPausedByApp = false }
    public func stop() {
        isSpeaking = false
        isPausedByApp = false
        currentUtterance = ""
    }
}

/// Native Apple speech only. Never uploads audio. Never claims speaker identity.
public final class SpeechPlayback: NSObject, AVSpeechSynthesizerDelegate, SpeechPlaying {
    nonisolated(unsafe) private let synthesizer = AVSpeechSynthesizer()
    private let log: EventLog
    nonisolated(unsafe) public weak var delegate: SpeechPlaybackDelegate?

    public private(set) var isSpeaking = false
    public private(set) var isPausedByApp = false
    public private(set) var currentUtterance = ""
    public var preferredVoiceIdentifier: String? {
        didSet { persistVoicePreference() }
    }

    public init(log: EventLog) {
        self.log = log
        super.init()
        synthesizer.delegate = self
        preferredVoiceIdentifier = UserDefaults.standard.string(forKey: "gate0.voiceIdentifier")
    }

    public func availableVoices(languagePrefix: String = "en") -> [AVSpeechSynthesisVoice] {
        AVSpeechSynthesisVoice.speechVoices().filter {
            $0.language.lowercased().hasPrefix(languagePrefix.lowercased())
        }
    }

    public func speak(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        stop()
        let utterance = AVSpeechUtterance(string: trimmed)
        if let preferredVoiceIdentifier,
           let voice = AVSpeechSynthesisVoice(identifier: preferredVoiceIdentifier) {
            utterance.voice = voice
        } else if let voice = AVSpeechSynthesisVoice(language: "en-US") {
            utterance.voice = voice
        }
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        currentUtterance = trimmed
        isPausedByApp = false
        synthesizer.speak(utterance)
        log.append(Gate0Event(kind: "speech_enqueue", detail: trimmed, draft: "", speaking: true))
    }

    public func pause() {
        guard synthesizer.isSpeaking, !synthesizer.isPaused else { return }
        synthesizer.pauseSpeaking(at: .word)
        isPausedByApp = true
        log.append(Gate0Event(kind: "speech_pause", detail: "", draft: "", speaking: true, paused: true))
    }

    public func resume() {
        guard synthesizer.isPaused else { return }
        synthesizer.continueSpeaking()
        isPausedByApp = false
        log.append(Gate0Event(kind: "speech_resume", detail: "", draft: "", speaking: true, paused: false))
    }

    public func stop() {
        guard synthesizer.isSpeaking || synthesizer.isPaused else {
            isSpeaking = false
            currentUtterance = ""
            return
        }
        synthesizer.stopSpeaking(at: .immediate)
        isSpeaking = false
        isPausedByApp = false
        log.append(Gate0Event(kind: "speech_stop", detail: "", draft: "", speaking: false, paused: false))
    }

    private func persistVoicePreference() {
        if let preferredVoiceIdentifier {
            UserDefaults.standard.set(preferredVoiceIdentifier, forKey: "gate0.voiceIdentifier")
        } else {
            UserDefaults.standard.removeObject(forKey: "gate0.voiceIdentifier")
        }
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didStart utterance: AVSpeechUtterance) {
        isSpeaking = true
        log.append(Gate0Event(kind: "speech_start", detail: utterance.speechString, speaking: true))
        delegate?.speechDidStart(utterance: utterance.speechString)
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        isSpeaking = false
        isPausedByApp = false
        log.append(Gate0Event(kind: "speech_finish", detail: utterance.speechString, speaking: false))
        delegate?.speechDidFinish(utterance: utterance.speechString, cancelled: false)
        currentUtterance = ""
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        isSpeaking = false
        isPausedByApp = false
        log.append(Gate0Event(kind: "speech_cancel", detail: utterance.speechString, speaking: false))
        delegate?.speechDidFinish(utterance: utterance.speechString, cancelled: true)
        currentUtterance = ""
    }
}
