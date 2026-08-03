import AppKit
import Foundation

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let model = IslandModel()
    private var panelController: PanelController?
    private var webSocket: WebSocketClient?
    private var parentTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        panelController = PanelController(model: model)

        let url = Self.webSocketURL()
        let client = WebSocketClient(url: url, model: model)
        model.sendDecision = { [weak client] event, approved in
            client?.sendDecision(for: event, approved: approved)
        }
        webSocket = client
        client.start()
        monitorParent()
    }

    func applicationWillTerminate(_ notification: Notification) {
        parentTimer?.invalidate()
        webSocket?.stop()
    }

    private static func webSocketURL() -> URL {
        let args = CommandLine.arguments
        if let index = args.firstIndex(of: "--websocket"), args.indices.contains(index + 1),
           let url = URL(string: args[index + 1]) {
            return url
        }
        return URL(string: "ws://127.0.0.1:17372")!
    }

    private func monitorParent() {
        guard let raw = ProcessInfo.processInfo.environment["DULUS_BAR_PARENT_PID"],
              let pid = Int32(raw) else { return }
        parentTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { _ in
            if kill(pid, 0) != 0 { NSApp.terminate(nil) }
        }
    }
}

MainActor.assumeIsolated {
    let application = NSApplication.shared
    let delegate = AppDelegate()
    application.delegate = delegate
    application.run()
    withExtendedLifetime(delegate) {}
}
