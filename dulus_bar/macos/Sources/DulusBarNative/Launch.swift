import Foundation

/// How the menu starts things. The Python hub tells us how to run its own
/// CLI (`DULUS_BAR_CLI`, a JSON argv), so an agent opened from the menu runs
/// under `dulusbar wrap` and shows up on the island, instead of starting
/// unwired in a terminal.
enum Launch {
    static func argv(_ key: String, in environment: [String: String] = ProcessInfo.processInfo.environment) -> [String]? {
        guard let raw = environment[key],
              let argv = try? JSONDecoder().decode([String].self, from: Data(raw.utf8)),
              !argv.isEmpty else { return nil }
        return argv
    }

    /// `dulusbar <arguments>`, when the hub said how to run it.
    static func cli(_ arguments: [String], in environment: [String: String] = ProcessInfo.processInfo.environment) -> [String]? {
        argv("DULUS_BAR_CLI", in: environment).map { $0 + arguments }
    }

    /// `command`, wired to the island under `name` (as-is without the CLI).
    static func wired(_ name: String, _ command: [String],
                      in environment: [String: String] = ProcessInfo.processInfo.environment) -> [String] {
        cli(["wrap", name, "--"] + command, in: environment) ?? command
    }

    /// Dulus through its own wrapper (it finds a local dulus.py too), else wired.
    static func dulus(in environment: [String: String] = ProcessInfo.processInfo.environment) -> [String] {
        argv("DULUS_BAR_OPEN_DULUS", in: environment) ?? wired("Dulus", ["dulus"], in: environment)
    }

    static func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    static func appleScriptString(_ value: String) -> String {
        "\"" + value.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }

    /// AppleScript that runs `argv` in a new Terminal window.
    static func terminalScript(_ argv: [String]) -> String {
        let line = argv.map(shellQuote).joined(separator: " ")
        return "tell application \"Terminal\"\n  do script \(appleScriptString(line))\n  activate\nend tell"
    }
}
