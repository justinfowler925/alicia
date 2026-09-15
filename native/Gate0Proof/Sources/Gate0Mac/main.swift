import AppKit
import Gate0Core

@main
enum Gate0MacMain {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller: ProofWindowController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let logs = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".brutus/gate0-logs", isDirectory: true)
        let log = EventLog(directory: logs)
        let session = ProofSession(log: log)
        let controller = ProofWindowController(session: session)
        self.controller = controller
        controller.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
        session.start()
        log.append(Gate0Event(kind: "mac_launch", detail: "logs=\(log.pathDescription)"))
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}
