import Foundation

/// Per-agent glyph + accent color — a mirror of `dulus_bar/agents.py`, so an
/// agent looks the same on the native island as on the Qt one.
struct AgentStyle: Equatable {
    let key: String
    let display: String
    let glyph: String
    let accent: UInt32  // 0xRRGGBB
    var aliases: [String] = []

    var isDulus: Bool { key == "dulus" }

    /// The accent, lifted toward white when it's too dark to read on the
    /// black island (Grok's near-black, Ollama's black…).
    var readableAccent: UInt32 { readableColor(accent) }

    static let known: [AgentStyle] = [
        AgentStyle(key: "dulus", display: "Dulus", glyph: "🦅", accent: 0xF59E0B, aliases: ["dulus", "interant"]),
        AgentStyle(key: "claude", display: "Claude Code", glyph: "✳️", accent: 0xD97757,
                   aliases: ["claude", "claude code", "anthropic"]),
        AgentStyle(key: "codex", display: "Codex", glyph: "◆", accent: 0x10A37F, aliases: ["codex", "openai", "gpt"]),
        AgentStyle(key: "gemini", display: "Gemini", glyph: "✦", accent: 0x4285F4, aliases: ["gemini", "google"]),
        AgentStyle(key: "cursor", display: "Cursor", glyph: "▸", accent: 0x7C3AED, aliases: ["cursor"]),
        AgentStyle(key: "copilot", display: "Copilot", glyph: "⧉", accent: 0x6E7681, aliases: ["copilot", "github copilot"]),
        AgentStyle(key: "kimi", display: "Kimi", glyph: "◐", accent: 0x2563EB, aliases: ["kimi", "moonshot"]),
        AgentStyle(key: "deepseek", display: "DeepSeek", glyph: "≋", accent: 0x4D6BFE, aliases: ["deepseek"]),
        AgentStyle(key: "grok", display: "Grok", glyph: "𝕏", accent: 0x111827, aliases: ["grok", "xai"]),
        AgentStyle(key: "qwen", display: "Qwen", glyph: "✺", accent: 0x615CED, aliases: ["qwen", "alibaba", "tongyi"]),
        AgentStyle(key: "mistral", display: "Mistral", glyph: "◢", accent: 0xFA5010, aliases: ["mistral", "codestral"]),
        AgentStyle(key: "windsurf", display: "Windsurf", glyph: "≈", accent: 0x09B6A2, aliases: ["windsurf", "codeium"]),
        AgentStyle(key: "zed", display: "Zed", glyph: "⚡", accent: 0x084CCF, aliases: ["zed"]),
        AgentStyle(key: "aider", display: "Aider", glyph: "✎", accent: 0x22C55E, aliases: ["aider"]),
        AgentStyle(key: "cline", display: "Cline", glyph: "◔", accent: 0x0EA5E9, aliases: ["cline", "roo"]),
        AgentStyle(key: "ollama", display: "Ollama", glyph: "◍", accent: 0x000000, aliases: ["ollama", "llama"]),
        AgentStyle(key: "opencode", display: "OpenCode", glyph: "◈", accent: 0xF97316, aliases: ["opencode"]),
        AgentStyle(key: "goose", display: "Goose", glyph: "🪿", accent: 0x8B5CF6, aliases: ["goose", "block goose"]),
        AgentStyle(key: "amp", display: "Amp", glyph: "⚡︎", accent: 0xFF2D20, aliases: ["amp", "sourcegraph"]),
        AgentStyle(key: "crush", display: "Crush", glyph: "♥", accent: 0xEC4899, aliases: ["crush", "charm"]),
        AgentStyle(key: "auggie", display: "Auggie", glyph: "Ⓐ", accent: 0x0D9488, aliases: ["auggie", "augment"]),
        AgentStyle(key: "continue", display: "Continue", glyph: "▶", accent: 0xFFDE59, aliases: ["continue", "cn"]),
        AgentStyle(key: "plandex", display: "Plandex", glyph: "⌘", accent: 0x84CC16, aliases: ["plandex"]),
    ]

    /// Unknown agents get a stable color from their name (same pick as Python).
    private static let fallbackPalette: [UInt32] = [
        0x38BDF8, 0xA78BFA, 0x34D399, 0xFBBF24, 0xFB7185, 0xE879F9, 0x5EEAD4, 0x93C5FD,
    ]

    /// Style for any agent name (never fails).
    static func of(_ agentName: String) -> AgentStyle {
        let name = agentName.trimmingCharacters(in: .whitespacesAndNewlines)
        let low = name.lowercased()
        if let exact = known.first(where: { $0.key == low || $0.aliases.contains(low) }) { return exact }
        for style in known {
            for alias in [style.key] + style.aliases where mentions(alias, in: low) { return style }
        }
        let index = low.unicodeScalars.reduce(0) { $0 + Int($1.value) } % fallbackPalette.count
        return AgentStyle(key: low.isEmpty ? "agent" : low, display: name.isEmpty ? "Agent" : name,
                          glyph: "●", accent: fallbackPalette[index])
    }

    /// `alias` appears in `name`; short ones ("cn", "amp", "zed", "gpt"…)
    /// only as a whole word: "Example Agent" is not Amp.
    static func mentions(_ alias: String, in name: String) -> Bool {
        guard alias.count <= 3 else { return name.contains(alias) }
        var searchStart = name.startIndex
        while searchStart < name.endIndex,
              let found = name.range(of: alias, options: [], range: searchStart..<name.endIndex) {
            let before = found.lowerBound > name.startIndex ? name[name.index(before: found.lowerBound)] : nil
            let after = found.upperBound < name.endIndex ? name[found.upperBound] : nil
            if !isWordCharacter(before) && !isWordCharacter(after) { return true }
            searchStart = name.index(after: found.lowerBound)
        }
        return false
    }

    private static func isWordCharacter(_ character: Character?) -> Bool {
        guard let character, character.isASCII else { return false }
        return character.isLetter || character.isNumber
    }
}

/// `color` (0xRRGGBB), mixed 60% toward white when its luminance is below
/// 0.4 — dark brand colors vanish on black (mirror of `agents.readable_accent`).
func readableColor(_ color: UInt32) -> UInt32 {
    let red = Double((color >> 16) & 0xFF), green = Double((color >> 8) & 0xFF), blue = Double(color & 0xFF)
    guard (0.299 * red + 0.587 * green + 0.114 * blue) / 255 < 0.4 else { return color }
    func lift(_ channel: Double) -> UInt32 { UInt32((channel + (255 - channel) * 0.6).rounded()) }
    return (lift(red) << 16) | (lift(green) << 8) | lift(blue)
}
