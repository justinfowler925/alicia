import UIKit
import Gate0Core

/// UITextView that reports composition state and attempts to observe dictation end.
final class InstrumentedTextView: UITextView, UITextViewDelegate {
    var onDraftSignal: ((String, Bool, Bool) -> Void)?
    var onFocusSignal: ((Bool) -> Void)?
    var onDictationEnd: (() -> Void)?

    override init(frame: CGRect, textContainer: NSTextContainer?) {
        super.init(frame: frame, textContainer: textContainer)
        delegate = self
        font = .preferredFont(forTextStyle: .body)
        autocorrectionType = .no
        isAccessibilityElement = true
        accessibilityLabel = AccessibilityNames.draftEditor
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func textViewDidChange(_ textView: UITextView) {
        emit()
    }

    func textViewDidBeginEditing(_ textView: UITextView) {
        onFocusSignal?(true)
        emit()
    }

    func textViewDidEndEditing(_ textView: UITextView) {
        onFocusSignal?(false)
        emit()
    }

    /// Documented UITextInput callback. Voice Control may or may not invoke it —
    /// Gate 0 logs whether it fires on real devices.
    override func dictationRecordingDidEnd() {
        super.dictationRecordingDidEnd()
        onDictationEnd?()
        emit()
    }

    private func emit() {
        let marked = markedTextRange != nil
        onDraftSignal?(text ?? "", marked, isFirstResponder)
    }
}

final class ProofViewController: UIViewController {
    private let session: ProofSession
    private let draft = InstrumentedTextView()
    private let status = UILabel()
    private let turns = UITextView()
    private let events = UITextView()
    private var applyingProgrammaticDraft = false

    init(session: ProofSession) {
        self.session = session
        super.init(nibName: nil, bundle: nil)
        title = "Brutus Gate 0"
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground

        status.numberOfLines = 0
        status.font = .preferredFont(forTextStyle: .subheadline)
        status.accessibilityLabel = AccessibilityNames.status
        status.text = session.statusText

        turns.isEditable = false
        events.isEditable = false
        events.accessibilityLabel = AccessibilityNames.eventLog
        turns.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        events.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        turns.backgroundColor = .secondarySystemBackground
        events.backgroundColor = .secondarySystemBackground
        draft.backgroundColor = .secondarySystemBackground
        draft.layer.cornerRadius = 8

        let pause = control(AccessibilityNames.pause, action: #selector(pauseTapped))
        let resume = control(AccessibilityNames.resume, action: #selector(resumeTapped))
        let stop = control(AccessibilityNames.stopSpeaking, action: #selector(stopTapped))
        let clear = control(AccessibilityNames.clearDraft, action: #selector(clearTapped))
        let force = control(AccessibilityNames.forceCommit, action: #selector(forceTapped))
        let buttons = UIStackView(arrangedSubviews: [pause, resume, stop, clear, force])
        buttons.axis = .horizontal
        buttons.spacing = 8
        buttons.distribution = .fillEqually

        let help = UILabel()
        help.numberOfLines = 0
        help.textColor = .secondaryLabel
        help.font = .preferredFont(forTextStyle: .footnote)
        help.text = "Enable Voice Control. Focus Brutus draft. Speak continuously for the 20-turn sample — no per-turn send. Pause is app-only; it does not sleep system Voice Control."

        let stack = UIStackView(arrangedSubviews: [
            help, status, buttons, labeled("Draft", draft),
            labeled("Turns", turns), labeled("Events", events),
        ])
        stack.axis = .vertical
        stack.spacing = 10
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 12),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 12),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -12),
            stack.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -12),
            draft.heightAnchor.constraint(equalToConstant: 100),
            turns.heightAnchor.constraint(equalToConstant: 120),
            events.heightAnchor.constraint(greaterThanOrEqualToConstant: 120),
        ])

        draft.onDraftSignal = { [weak self] text, marked, focused in
            guard let self, !self.applyingProgrammaticDraft else { return }
            self.session.updateDraft(text, markedTextActive: marked, focused: focused)
        }
        draft.onFocusSignal = { [weak self] focused in
            self?.session.noteFocus(focused)
        }
        draft.onDictationEnd = { [weak self] in
            guard let self else { return }
            self.session.log.append(Gate0Event(kind: "dictation_callback", detail: "dictationRecordingDidEnd"))
            self.session.updateDraft(
                self.draft.text ?? "",
                markedTextActive: self.draft.markedTextRange != nil,
                focused: self.draft.isFirstResponder,
                dictationCallbackFired: true
            )
        }
        session.onStatusChange = { [weak self] text in
            DispatchQueue.main.async { self?.status.text = text }
        }
        session.onTurnsChange = { [weak self] turns in
            DispatchQueue.main.async {
                self?.turns.text = turns.enumerated().map { idx, turn in
                    "\(idx + 1). [\(turn.commitReason)] \(turn.userText)"
                }.joined(separator: "\n")
            }
        }
        session.onClearDraft = { [weak self] in
            DispatchQueue.main.async { self?.setDraft("") }
        }
        session.log.onAppend = { [weak self] event in
            DispatchQueue.main.async {
                guard let self else { return }
                let line = "\(event.kind) \(event.detail)"
                self.events.text = (self.events.text ?? "").isEmpty
                    ? line
                    : (self.events.text ?? "") + "\n" + line
            }
        }
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        draft.becomeFirstResponder()
    }

    @objc private func pauseTapped() { session.setAppPaused(true) }
    @objc private func resumeTapped() { session.setAppPaused(false) }
    @objc private func stopTapped() {
        session.speech.stop()
        session.log.append(Gate0Event(kind: "stop_speaking_control"))
    }
    @objc private func clearTapped() { setDraft("") }
    @objc private func forceTapped() { session.forceCommit() }

    private func setDraft(_ text: String) {
        applyingProgrammaticDraft = true
        draft.text = text
        applyingProgrammaticDraft = false
        session.updateDraft(text, markedTextActive: false, focused: draft.isFirstResponder)
    }

    private func control(_ title: String, action: Selector) -> UIButton {
        let button = UIButton(type: .system)
        button.setTitle(title, for: .normal)
        button.accessibilityLabel = title
        button.addTarget(self, action: action, for: .touchUpInside)
        return button
    }

    private func labeled(_ title: String, _ view: UIView) -> UIStackView {
        let label = UILabel()
        label.text = title
        label.font = .preferredFont(forTextStyle: .headline)
        let stack = UIStackView(arrangedSubviews: [label, view])
        stack.axis = .vertical
        stack.spacing = 4
        return stack
    }
}
