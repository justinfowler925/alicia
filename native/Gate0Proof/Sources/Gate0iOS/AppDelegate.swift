import UIKit
import Gate0Core

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    private var session: ProofSession?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        let logs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("gate0-logs", isDirectory: true)
        let log = EventLog(directory: logs)
        let session = ProofSession(log: log)
        self.session = session
        let root = ProofViewController(session: session)
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = UINavigationController(rootViewController: root)
        window.makeKeyAndVisible()
        self.window = window
        session.start()
        log.append(Gate0Event(kind: "ios_launch", detail: "logs=\(log.pathDescription)"))
        return true
    }
}
