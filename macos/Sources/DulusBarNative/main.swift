import AppKit
import Foundation

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let model = IslandModel()
    private var panelController: PanelController?
    private var webSocket: WebSocketClient?
    private var parentTimer: Timer?
    private var statusItem: NSStatusItem?
    private var brandImage: NSImage?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        configureBranding()
        configureStatusItem()
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

    private func configureBranding() {
        guard let url = Bundle.module.url(
            forResource: "dulus-bird",
            withExtension: "png"
        ), let image = NSImage(contentsOf: url) else { return }

        image.isTemplate = false
        brandImage = image
        NSApp.applicationIconImage = image
    }

    private func configureStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = item.button {
            button.image = brandImage
            button.image?.size = NSSize(width: 19, height: 19)
            button.imageScaling = .scaleProportionallyDown
            button.toolTip = "Dulus Bar"
        }

        let menu = NSMenu()
        menu.addItem(withTitle: "Show island", action: #selector(showIsland), keyEquivalent: "")
        menu.addItem(withTitle: "Open Dulus Bar folder", action: #selector(openProjectFolder), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit Dulus Bar", action: #selector(quitApplication), keyEquivalent: "q")
        menu.items.forEach { $0.target = self }
        item.menu = menu
        statusItem = item
    }

    @objc private func showIsland() {
        model.reveal()
    }

    @objc private func openProjectFolder() {
        let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        NSWorkspace.shared.open(root)
    }

    @objc private func quitApplication() {
        NSApp.terminate(nil)
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
