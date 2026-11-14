import Foundation

/// Collapse whitespace to one line and truncate with an ellipsis.
func oneLine(_ text: String, limit: Int = 200) -> String {
    let flat = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    return flat.count <= limit ? flat : String(flat.prefix(limit - 1)) + "…"
}

/// One clean representation of a tool call; never the same text twice.
func callText(_ tool: String, _ args: String, _ separator: String = "  ") -> String {
    let tool = tool.trimmingCharacters(in: .whitespacesAndNewlines)
    let args = args.trimmingCharacters(in: .whitespacesAndNewlines)
    if args.isEmpty { return tool }
    if tool.isEmpty { return args }
    if args.contains(tool) || tool.contains(args) { return args.count >= tool.count ? args : tool }
    return tool + separator + args
}

/// Show characters that hide or reorder text as `⟨U+202E⟩` (mirror of
/// `dulus_bar.text.reveal_hidden`). A right-to-left override can make a
/// command *read* differently than it *runs*; what you approve must read
/// exactly as it runs. Newlines and tabs stay; CRLF is just a line break.
func revealHidden(_ text: String) -> String {
    var out = String.UnicodeScalarView()
    for scalar in text.replacingOccurrences(of: "\r\n", with: "\n").unicodeScalars {
        let value = scalar.value
        let hides = (value < 0x20 && value != 0x0A && value != 0x09)
            || (0x7F..<0xA0).contains(value)
            || value == 0x061C || value == 0x200E || value == 0x200F
            || (0x202A...0x202E).contains(value) || (0x2066...0x2069).contains(value)
        if hides {
            out.append(contentsOf: String(format: "⟨U+%04X⟩", value).unicodeScalars)
        } else {
            out.append(scalar)
        }
    }
    return String(out)
}

/// Hard-wrap a code block at `columns` — at a space when there's one in the
/// second half of the line — and keep at most `maxLines` lines, saying how
/// many were left out. A long command is wrapped, never cut off sideways.
func wrapCode(_ text: String, columns: Int, maxLines: Int) -> String {
    let columns = max(columns, 8)
    var visual: [String] = []
    for line in text.replacingOccurrences(of: "\t", with: "    ").components(separatedBy: "\n") {
        var rest = Substring(line)
        while rest.count > columns {
            let window = rest.prefix(columns)
            var cut = window.endIndex
            if let space = window.lastIndex(of: " "), window.distance(from: window.startIndex, to: space) >= columns / 2 {
                cut = window.index(after: space)
            }
            visual.append(String(rest[rest.startIndex..<cut]))
            rest = rest[cut...]
        }
        visual.append(String(rest))
    }
    if visual.count > maxLines {
        let hidden = visual.count - (maxLines - 1)
        visual = Array(visual.prefix(maxLines - 1)) + ["… \(hidden) more lines"]
    }
    return visual.joined(separator: "\n")
}

/// "12s", "4m", "2h" — how long a session has been in its state.
func shortDuration(_ seconds: TimeInterval) -> String {
    let seconds = max(0, Int(seconds))
    if seconds < 60 { return "\(seconds)s" }
    if seconds < 3600 { return "\(seconds / 60)m" }
    return "\(seconds / 3600)h"
}

/// How full a context is, for coloring "ctx 38%": nil for token counts ("82k").
func contextPercent(_ context: String) -> Int? {
    guard context.contains("%") else { return nil }
    let digits = context.drop(while: { !$0.isNumber }).prefix(while: { $0.isNumber })
    return Int(digits)
}
