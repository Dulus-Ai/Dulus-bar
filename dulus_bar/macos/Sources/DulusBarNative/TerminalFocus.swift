import AppKit
import Darwin

/// Clicking a session brings up the terminal or editor it runs in: walk up
/// from the agent's process to the first ancestor that's a regular app
/// (Terminal, iTerm, Ghostty, VS Code, Cursor…) and bring that app forward.
@MainActor
enum TerminalFocus {
    /// The parent of `pid`, from the kernel's process table.
    static func parent(of pid: pid_t) -> pid_t? {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
        guard sysctl(&mib, u_int(mib.count), &info, &size, nil, 0) == 0, size > 0 else { return nil }
        let parent = info.kp_eproc.e_ppid
        return parent > 1 ? parent : nil
    }

    /// The app hosting `pid`: the process itself or its nearest ancestor
    /// that's a regular (Dock) app — a shell or helper never is.
    static func hostingApp(of pid: pid_t) -> NSRunningApplication? {
        var current: pid_t? = pid
        for _ in 0..<24 {
            guard let candidate = current else { return nil }
            if let app = NSRunningApplication(processIdentifier: candidate), app.activationPolicy == .regular {
                return app
            }
            current = parent(of: candidate)
        }
        return nil
    }

    /// Terminals whose sessions live in a daemon outside the app's process
    /// tree (iTerm2's iTermServer belongs to launchd): first one running wins.
    static let knownTerminals = [
        "com.googlecode.iterm2", "com.mitchellh.ghostty", "dev.warp.Warp-Stable", "com.apple.Terminal",
    ]

    static func runningKnownTerminal() -> NSRunningApplication? {
        for bundleID in knownTerminals {
            if let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first { return app }
        }
        return nil
    }

    @discardableResult
    static func focus(pid: pid_t) -> Bool {
        guard let app = hostingApp(of: pid) ?? runningKnownTerminal() else { return false }
        if let url = app.bundleURL {
            // Opening a running app brings it forward the way the Dock does,
            // which still works where activate() from a background app is refused.
            let configuration = NSWorkspace.OpenConfiguration()
            configuration.activates = true
            NSWorkspace.shared.openApplication(at: url, configuration: configuration, completionHandler: nil)
            return true
        }
        return app.activate(options: [.activateIgnoringOtherApps])
    }
}
