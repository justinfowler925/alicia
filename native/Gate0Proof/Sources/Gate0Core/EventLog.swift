import Foundation

/// Structured instrumentation for Gate 0 go/no-go evidence.
/// Persists one JSONL file per run so physical Voice Control samples are auditable.
public struct Gate0Event: Codable, Sendable, Equatable {
    public let at: Date
    public let kind: String
    public let detail: String
    public let draft: String
    public let speaking: Bool
    public let paused: Bool
    public let markedTextActive: Bool

    public init(
        kind: String,
        detail: String = "",
        draft: String = "",
        speaking: Bool = false,
        paused: Bool = false,
        markedTextActive: Bool = false,
        at: Date = Date()
    ) {
        self.at = at
        self.kind = kind
        self.detail = detail
        self.draft = draft
        self.speaking = speaking
        self.paused = paused
        self.markedTextActive = markedTextActive
    }
}

public final class EventLog: @unchecked Sendable {
    private let lock = NSLock()
    private var events: [Gate0Event] = []
    private let fileURL: URL?
    private let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = [.sortedKeys]
        return e
    }()

    public var onAppend: ((Gate0Event) -> Void)?

    public init(directory: URL? = nil) {
        if let directory {
            try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
            self.fileURL = directory.appendingPathComponent("gate0-\(stamp).jsonl")
        } else {
            self.fileURL = nil
        }
    }

    public var all: [Gate0Event] {
        lock.lock(); defer { lock.unlock() }
        return events
    }

    public var pathDescription: String {
        fileURL?.path ?? "(memory only)"
    }

    public func append(_ event: Gate0Event) {
        lock.lock()
        events.append(event)
        lock.unlock()
        if let fileURL, let data = try? encoder.encode(event),
           var line = String(data: data, encoding: .utf8) {
            line.append("\n")
            if let bytes = line.data(using: .utf8) {
                if FileManager.default.fileExists(atPath: fileURL.path) {
                    if let handle = try? FileHandle(forWritingTo: fileURL) {
                        defer { try? handle.close() }
                        _ = try? handle.seekToEnd()
                        try? handle.write(contentsOf: bytes)
                    }
                } else {
                    try? bytes.write(to: fileURL, options: .atomic)
                }
            }
        }
        onAppend?(event)
    }
}
