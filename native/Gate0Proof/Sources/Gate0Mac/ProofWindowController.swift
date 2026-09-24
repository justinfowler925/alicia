import AppKit
import Gate0Core

/// NSTextView that surfaces composition/marked-text and focus for Gate 0 instrumentation.
final class InstrumentedTextView: NSTextView {
    var onDraftSignal: ((String, Bool, Bool) -> Void)?
    var onFocusSignal: ((Bool) -> Void)?

    override func didChangeText() {
        super.didChangeText()
        emit()
    }

    override func setMarkedText(_ string: Any, selectedRange: NSRange, replacementRange: NSRange) {
        super.setMarkedText(string, selectedRange: selectedRange, replacementRange: replacementRange)
        emit()
    }

    override func unmarkText() {
        super.unmarkText()
        emit()
    }

    override func becomeFirstResponder() -> Bool {
        let ok = super.becomeFirstResponder()
        if ok { onFocusSignal?(true) }
        return ok
    }

    override func resignFirstResponder() -> Bool {
        let ok = super.resignFirstResponder()
        if ok { onFocusSignal?(false) }
        return ok
    }

    private func emit() {
        let marked = hasMarkedText()
        onDraftSignal?(string, marked, window?.firstResponder === self)
    }
}

final class ProofWindowController: NSWindowController, NSWindowDelegate {
    private let session: ProofSession
    private let draftView: InstrumentedTextView
    private let statusLabel = NSTextField(labelWithString: "")
    private let turnsView = NSTextView()
    private let eventsView = NSTextView()
    private var applyingProgrammaticDraft = false

    init(session: ProofSession) {
        self.session = session
        let scroll = NSScrollView()
        let draft = InstrumentedTextView(frame: NSRect(x: 0, y: 0, width: 640, height: 120))
        draft.isRichText = false
        draft.allowsUndo = true
        draft.font = .systemFont(ofSize: 18)
        draft.setAccessibilityElement(true)
        draft.setAccessibilityRole(.textArea)
        draft.setAccessibilityLabel(AccessibilityNames.draftEditor)
        draft.isEditable = true
        draft.isSelectable = true
        scroll.documentView = draft
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        self.draftView = draft

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 780, height: 640),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Alicia Gate 0 — Voice Control proof"
        window.center()
        super.init(window: window)
        window.delegate = self

        let root = NSView(frame: window.contentView!.bounds)
        root.autoresizingMask = [.width, .height]
        window.contentView = root

        statusLabel.stringValue = session.statusText
        statusLabel.font = .systemFont(ofSize: 13, weight: .medium)
        statusLabel.setAccessibilityLabel(AccessibilityNames.status)
        statusLabel.lineBreakMode = .byWordWrapping
        statusLabel.maximumNumberOfLines = 3

        let pause = button(AccessibilityNames.pause, action: #selector(pauseClicked))
        let resume = button(AccessibilityNames.resume, action: #selector(resumeClicked))
        let stop = button(AccessibilityNames.stopSpeaking, action: #selector(stopClicked))
        let clear = button(AccessibilityNames.clearDraft, action: #selector(clearClicked))
        let force = button(AccessibilityNames.forceCommit, action: #selector(forceClicked))

        let buttonRow = NSStackView(views: [pause, resume, stop, clear, force])
        buttonRow.orientation = .horizontal
        buttonRow.spacing = 8

        configureReadOnly(turnsView)
        configureReadOnly(eventsView)
        eventsView.setAccessibilityLabel(AccessibilityNames.eventLog)

        let turnsScroll = wrap(turnsView)
        let eventsScroll = wrap(eventsView)
        let draftLabel = NSTextField(labelWithString: "Draft (Voice Control target — named “Alicia draft”)")
        draftLabel.font = .boldSystemFont(ofSize: 12)
        let turnsLabel = NSTextField(labelWithString: "Committed turns")
        turnsLabel.font = .boldSystemFont(ofSize: 12)
        let eventsLabel = NSTextField(labelWithString: "Event log (also ~/.alicia/gate0-logs)")
        eventsLabel.font = .boldSystemFont(ofSize: 12)

        let help = NSTextField(wrappingLabelWithString: """
        Gate 0 proof. Enable macOS Voice Control. Focus the draft. Speak continuously — the acceptance sample forbids a per-turn send command. Pause/Resume are app controls only; they do not disable the OS microphone. Force commit is diagnostic only.
        """)
        help.textColor = .secondaryLabelColor

        let stack = NSStackView(views: [
            help, statusLabel, buttonRow, draftLabel, scroll,
            turnsLabel, turnsScroll, eventsLabel, eventsScroll,
        ])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: root.topAnchor, constant: 16),
            stack.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 16),
            stack.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -16),
            stack.bottomAnchor.constraint(equalTo: root.bottomAnchor, constant: -16),
            scroll.heightAnchor.constraint(equalToConstant: 120),
            turnsScroll.heightAnchor.constraint(equalToConstant: 140),
            eventsScroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 140),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            turnsScroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            eventsScroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            buttonRow.widthAnchor.constraint(equalTo: stack.widthAnchor),
        ])

        draftView.onDraftSignal = { [weak self] text, marked, focused in
            guard let self, !self.applyingProgrammaticDraft else { return }
            self.session.updateDraft(text, markedTextActive: marked, focused: focused)
        }
        draftView.onFocusSignal = { [weak self] focused in
            guard let self else { return }
            self.session.noteFocus(focused)
            self.session.updateDraft(
                self.draftView.string,
                markedTextActive: self.draftView.hasMarkedText(),
                focused: focused
            )
        }
        session.onStatusChange = { [weak self] text in
            DispatchQueue.main.async { self?.statusLabel.stringValue = text }
        }
        session.onTurnsChange = { [weak self] turns in
            DispatchQueue.main.async {
                self?.turnsView.string = turns.enumerated().map { idx, turn in
                    "\(idx + 1). [\(turn.commitReason)] you: \(turn.userText)\n   reply: \(turn.replyText)"
                }.joined(separator: "\n")
            }
        }
        session.onClearDraft = { [weak self] in
            DispatchQueue.main.async { self?.setDraft("") }
        }
        session.log.onAppend = { [weak self] event in
            DispatchQueue.main.async {
                guard let self else { return }
                let line = "\(Self.timeFormatter.string(from: event.at)) \(event.kind) \(event.detail)"
                self.eventsView.string = self.eventsView.string.isEmpty
                    ? line
                    : self.eventsView.string + "\n" + line
                self.eventsView.scrollToEndOfDocument(nil)
            }
        }

        window.makeFirstResponder(draftView)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    @objc private func pauseClicked() { session.setAppPaused(true) }
    @objc private func resumeClicked() { session.setAppPaused(false) }
    @objc private func stopClicked() {
        session.speech.stop()
        session.log.append(Gate0Event(kind: "stop_speaking_control"))
    }
    @objc private func clearClicked() { setDraft("") }
    @objc private func forceClicked() { session.forceCommit() }

    private func setDraft(_ text: String) {
        applyingProgrammaticDraft = true
        draftView.string = text
        applyingProgrammaticDraft = false
        session.updateDraft(text, markedTextActive: false, focused: window?.firstResponder === draftView)
    }

    private func button(_ title: String, action: Selector) -> NSButton {
        let button = NSButton(title: title, target: self, action: action)
        button.setAccessibilityLabel(title)
        button.bezelStyle = .rounded
        return button
    }

    private func configureReadOnly(_ view: NSTextView) {
        view.isEditable = false
        view.isSelectable = true
        view.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
    }

    private func wrap(_ view: NSTextView) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.documentView = view
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        return scroll
    }

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss.SSS"
        return f
    }()
}
