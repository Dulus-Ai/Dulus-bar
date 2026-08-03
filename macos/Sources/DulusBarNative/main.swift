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
        menu.addItem(withTitle: "Open Dulus", action: #selector(openDulus), keyEquivalent: "")
        menu.addItem(makeAgentMenuItem())
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

    private func makeAgentMenuItem() -> NSMenuItem {
        let root = NSMenuItem(title: "Open agent…", action: nil, keyEquivalent: "")
        let submenu = NSMenu(title: "Open agent…")
        let agents = [
            ("Claude Code", "claude"),
            ("Codex", "codex"),
            ("Gemini", "gemini"),
            ("OpenCode", "opencode"),
            ("Aider", "aider"),
            ("Ollama", "ollama")
        ]
        var found = false
        for (title, command) in agents where executableExists(command) {
            let item = NSMenuItem(title: title, action: #selector(openKnownAgent(_:)), keyEquivalent: "")
            item.representedObject = command
            item.target = self
            submenu.addItem(item)
            found = true
        }
        if found { submenu.addItem(.separator()) }
        let custom = NSMenuItem(title: "Choose any AI or executable…", action: #selector(chooseAgent), keyEquivalent: "")
        custom.target = self
        submenu.addItem(custom)
        root.submenu = submenu
        return root
    }

    private func executableExists(_ command: String) -> Bool {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/which")
        task.arguments = [command]
        task.standardOutput = FileHandle.nullDevice
        task.standardError = FileHandle.nullDevice
        do {
            try task.run()
            task.waitUntilExit()
            return task.terminationStatus == 0
        } catch {
            return false
        }
    }

    @objc private func openDulus() {
        launchInTerminal(command: "dulus")
    }

    @objc private func openKnownAgent(_ sender: NSMenuItem) {
        guard let command = sender.representedObject as? String else { return }
        launchInTerminal(command: command)
    }

    @objc private func chooseAgent() {
        let picker = NSOpenPanel()
        picker.title = "Choose any AI CLI or executable"
        picker.prompt = "Open in Terminal"
        picker.canChooseFiles = true
        picker.canChooseDirectories = false
        picker.allowsMultipleSelection = false
        guard picker.runModal() == .OK, let url = picker.url else { return }
        launchInTerminal(command: shellQuote(url.path))
    }

    private func launchInTerminal(command: String) {
        let script = "tell application \"Terminal\" to do script \(String(reflecting: command))\ntell application \"Terminal\" to activate"
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        task.arguments = ["-e", script]
        try? task.run()
    }

    private func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
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
