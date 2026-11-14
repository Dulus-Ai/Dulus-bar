import AppKit
import SwiftUI

// MARK: - Look

extension Color {
    /// From 0xRRGGBB.
    init(hex: UInt32, opacity: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: opacity
        )
    }
}

/// The same colors as the Qt island, so a status reads the same everywhere.
enum Palette {
    static let running = Color(hex: 0x4ADE80)
    static let waiting = Color(hex: 0xFACC15)
    static let done = Color(hex: 0x60A5FA)
    static let error = Color(hex: 0xF87171)
    static let idle = Color(hex: 0x71717A)
    /// What Allow fills with while you hold it for "always".
    static let always = Color(hex: 0x30D158)
    static let brand = Color(hex: 0xFF7A1F)

    static func status(_ status: String) -> Color {
        switch status {
        case "waiting": return waiting
        case "error": return error
        case "done": return done
        case "running": return running
        default: return idle
        }
    }

    /// "ctx 38%": green → amber → red as it fills; token counts stay neutral.
    static func context(_ context: String) -> Color {
        guard let percent = contextPercent(context) else { return Color.white.opacity(0.5) }
        if percent >= 85 { return error }
        if percent >= 60 { return waiting }
        return running
    }
}

extension AgentStyle {
    var color: Color { Color(hex: readableAccent) }
}

enum IslandLayout {
    /// Content width of the open island; the black card adds the flares.
    static let width: CGFloat = 404
    static let padding: CGFloat = 16
    /// How far the open island's top corners flare out into the menu bar.
    static let flare: CGFloat = 12
    static let radius: CGFloat = 26
    /// The little ledge under the notch that carries one light per live agent.
    static let chinHeight: CGFloat = 11
    static let maxRows = 4
    static let codeFontSize: CGFloat = 10.5
    static let codePadding: CGFloat = 9
    static let codeLines = 7
    /// Characters per line of the code block, from SF Mono's real advance.
    static let codeColumns: Int = {
        let font = NSFont.monospacedSystemFont(ofSize: IslandLayout.codeFontSize, weight: .regular)
        let advance = ("M" as NSString).size(withAttributes: [.font: font]).width
        let room = IslandLayout.width - 2 * IslandLayout.padding - 2 * IslandLayout.codePadding - 2
        return max(24, Int(room / max(advance, 1)))
    }()
}

// MARK: - The silhouette

/// The island's shape: a top edge flush with the screen (its corners flaring
/// out into the menu bar when it's open, like it pours out of the notch),
/// rounded bottom corners, and an optional ledge ("chin") under the middle.
struct IslandShape: Shape {
    var flare: CGFloat
    var radius: CGFloat
    var chinWidth: CGFloat = 0
    var chinHeight: CGFloat = 0

    var animatableData: AnimatablePair<AnimatablePair<CGFloat, CGFloat>, AnimatablePair<CGFloat, CGFloat>> {
        get { AnimatablePair(AnimatablePair(flare, radius), AnimatablePair(chinWidth, chinHeight)) }
        set {
            flare = newValue.first.first
            radius = newValue.first.second
            chinWidth = newValue.second.first
            chinHeight = newValue.second.second
        }
    }

    func path(in rect: CGRect) -> Path {
        let chinHeight = min(max(self.chinHeight, 0), rect.height / 2)
        let bottom = rect.maxY - chinHeight
        let flare = max(0, min(self.flare, rect.width / 4, (bottom - rect.minY) / 2))
        let left = rect.minX + flare
        let right = rect.maxX - flare
        let radius = max(0, min(self.radius, (right - left) / 2, bottom - rect.minY - flare))

        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addQuadCurve(to: CGPoint(x: left, y: rect.minY + flare), control: CGPoint(x: left, y: rect.minY))
        path.addLine(to: CGPoint(x: left, y: bottom - radius))
        path.addQuadCurve(to: CGPoint(x: left + radius, y: bottom), control: CGPoint(x: left, y: bottom))

        let chinHalf = min(chinWidth / 2, (right - left) / 2 - radius - 1)
        if chinHeight > 0.5 && chinHalf > 2 {
            let chinLeft = rect.midX - chinHalf
            let chinRight = rect.midX + chinHalf
            let chinBottom = bottom + chinHeight
            let fillet = max(0, min(3, chinHeight / 2, chinLeft - (left + radius)))
            let corner = max(0, min(chinHeight - fillet, chinHalf))
            path.addLine(to: CGPoint(x: chinLeft - fillet, y: bottom))
            path.addQuadCurve(to: CGPoint(x: chinLeft, y: bottom + fillet), control: CGPoint(x: chinLeft, y: bottom))
            path.addLine(to: CGPoint(x: chinLeft, y: chinBottom - corner))
            path.addQuadCurve(to: CGPoint(x: chinLeft + corner, y: chinBottom), control: CGPoint(x: chinLeft, y: chinBottom))
            path.addLine(to: CGPoint(x: chinRight - corner, y: chinBottom))
            path.addQuadCurve(to: CGPoint(x: chinRight, y: chinBottom - corner), control: CGPoint(x: chinRight, y: chinBottom))
            path.addLine(to: CGPoint(x: chinRight, y: bottom + fillet))
            path.addQuadCurve(to: CGPoint(x: chinRight + fillet, y: bottom), control: CGPoint(x: chinRight, y: bottom))
        }

        path.addLine(to: CGPoint(x: right - radius, y: bottom))
        path.addQuadCurve(to: CGPoint(x: right, y: bottom - radius), control: CGPoint(x: right, y: bottom))
        path.addLine(to: CGPoint(x: right, y: rect.minY + flare))
        path.addQuadCurve(to: CGPoint(x: rect.maxX, y: rect.minY), control: CGPoint(x: right, y: rect.minY))
        path.closeSubpath()
        return path
    }
}

// MARK: - Small pieces

/// A status dot. Only a waiting one ripples: an island shouldn't animate all
/// day just because an agent is busy.
struct StatusLight: View {
    let status: String
    var size: CGFloat = 6

    var body: some View {
        let color = Palette.status(status)
        Circle()
            .fill(color)
            .frame(width: size, height: size)
            .shadow(color: color.opacity(0.55), radius: size * 0.6)
            .overlay {
                if status == "waiting" { Ripple(color: color) }
            }
            .accessibilityLabel(Text(verbatim: status))
    }
}

private struct Ripple: View {
    let color: Color
    @State private var spread = false

    var body: some View {
        Circle()
            .stroke(color, lineWidth: 1)
            .scaleEffect(spread ? 2.6 : 1)
            .opacity(spread ? 0 : 0.75)
            .onAppear {
                withAnimation(.easeOut(duration: 1.1).repeatForever(autoreverses: false)) { spread = true }
            }
    }
}

/// An agent's glyph in a tinted circle (the bird for Dulus), its status on the rim.
struct AgentBadge: View {
    let style: AgentStyle
    let status: String
    var size: CGFloat = 28

    var body: some View {
        ZStack {
            Circle().fill(style.color.opacity(0.18))
            if style.isDulus {
                Image("dulus-bird", bundle: .module)
                    .resizable()
                    .scaledToFit()
                    .padding(size * 0.14)
            } else {
                Text(verbatim: style.glyph)
                    .font(.system(size: size * 0.48, weight: .semibold))
                    .foregroundStyle(style.color)
            }
        }
        .frame(width: size, height: size)
        .overlay(alignment: .bottomTrailing) {
            StatusLight(status: status, size: max(5, size * 0.26))
                .padding(1.5)
                .background(Circle().fill(Color.black))
                .offset(x: 2, y: 2)
        }
        .accessibilityHidden(true)
    }
}

/// "1 of 3": more requests are lined up behind this one.
struct QueueBadge: View {
    let position: Int
    let total: Int

    var body: some View {
        Text(verbatim: "\(position) of \(total)")
            .font(.system(size: 9.5, weight: .bold, design: .rounded))
            .foregroundStyle(Color.black)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(Capsule().fill(Palette.waiting))
            .accessibilityLabel(Text(verbatim: "\(total) requests waiting"))
    }
}

struct IslandButtonStyle: ButtonStyle {
    var primary = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: .semibold, design: .rounded))
            .foregroundStyle(primary ? Color.black : Color.white.opacity(0.85))
            .frame(maxWidth: .infinity)
            .frame(height: 30)
            .background(
                Capsule().fill(primary ? Color.white : Color.white.opacity(configuration.isPressed ? 0.2 : 0.12))
            )
            .contentShape(Capsule())
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

// MARK: - Allow: click once, hold for always

/// Allow. A click allows this once; when the agent offers it, *holding*
/// Allow always allows: the button fills up, the trackpad taps once, done.
/// Drag off the button to back out. Right-click has both, spelled out.
///
/// The rules live in `HoldGate`: a hold that fired never counts as a click
/// on release (so it can't approve the next card in the queue), and a press
/// that started before the card was armed counts for nothing.
@MainActor
struct HoldToAllowButton: View {
    let canAlways: Bool
    let alwaysLabel: String
    let armed: Bool
    let onAllow: () -> Void
    let onAlways: () -> Void

    /// How long to hold for "always".
    static let holdSeconds = 0.8
    /// Shorter than this is a click: no fill, so a click never flashes green.
    static let fillDelay = 0.14

    @State private var gate = HoldGate(canAlways: false)
    @State private var ignoringPress = false
    @State private var filling = false
    @State private var fill: CGFloat = 0
    @State private var confirmed = false
    @State private var holdTask: Task<Void, Never>?

    var body: some View {
        GeometryReader { geometry in
            let bounds = CGRect(origin: .zero, size: geometry.size)
            ZStack(alignment: .leading) {
                Capsule().fill(Color.white)
                Rectangle()
                    .fill(Palette.always)
                    .frame(width: geometry.size.width * fill)
                label.frame(maxWidth: .infinity)
            }
            .clipShape(Capsule())
            .contentShape(Capsule())
            .gesture(press(in: bounds.insetBy(dx: -8, dy: -8)))
        }
        .frame(height: 30)
        .onDisappear { holdTask?.cancel() }
        .contextMenu { menu }
        .help(canAlways ? "Click to allow once · hold to always allow" : "Allow")
        .accessibilityElement(children: .ignore)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel(Text("Allow"))
        .accessibilityHint(Text(verbatim: canAlways ? "Hold to always allow \(alwaysLabel)" : ""))
        .accessibilityAction { if armed { onAllow() } }
        .alwaysAllowAction(canAlways ? onAlways : nil)
    }

    private var label: some View {
        HStack(spacing: 5) {
            if confirmed {
                Image(systemName: "checkmark")
                    .font(.system(size: 10.5, weight: .heavy))
            }
            Text(confirmed ? "Always allowed" : (filling ? "Always allow" : "Allow"))
        }
        .font(.system(size: 12, weight: .semibold, design: .rounded))
        .foregroundStyle(Color.black)
        .lineLimit(1)
    }

    @ViewBuilder
    private var menu: some View {
        Button("Allow once", action: onAllow)
        if canAlways {
            Button(action: onAlways) {
                Text(verbatim: alwaysLabel.isEmpty ? "Always allow" : "Always allow \(alwaysLabel)")
            }
        }
    }

    private func press(in bounds: CGRect) -> some Gesture {
        DragGesture(minimumDistance: 0, coordinateSpace: .local)
            .onChanged { value in
                if !gate.pressing && !ignoringPress {
                    // A press that began before the card was armed, or a
                    // ⌃-click (that one opens the menu), counts for nothing.
                    guard armed, !NSEvent.modifierFlags.contains(.control) else {
                        ignoringPress = true
                        return
                    }
                    begin()
                }
                if !ignoringPress && !bounds.contains(value.location) { abandon() }
            }
            .onEnded { value in
                defer { ignoringPress = false }
                guard !ignoringPress else { return }
                holdTask?.cancel()
                let outcome = gate.release(inside: bounds.contains(value.location))
                if !confirmed { settle() }
                if outcome == .allow { onAllow() }
            }
    }

    private func begin() {
        gate = HoldGate(canAlways: canAlways)
        gate.press()
        guard canAlways else { return }
        holdTask = Task { @MainActor in
            try? await Task.sleep(for: .seconds(Self.fillDelay))
            guard !Task.isCancelled, gate.holding else { return }
            filling = true
            withAnimation(.linear(duration: Self.holdSeconds - Self.fillDelay)) { fill = 1 }
            try? await Task.sleep(for: .seconds(Self.holdSeconds - Self.fillDelay))
            guard !Task.isCancelled else { return }
            // Only while the button is really still down: a press whose end
            // got lost (a menu took the mouse) must never turn into "always".
            if gate.holdElapsed(buttonDown: NSEvent.pressedMouseButtons & 1 == 1) == .always {
                commitAlways()
            }
        }
    }

    private func abandon() {
        guard gate.holding else { return }
        gate.leave()
        holdTask?.cancel()
        settle()
    }

    private func settle() {
        filling = false
        withAnimation(.easeOut(duration: 0.18)) { fill = 0 }
    }

    private func commitAlways() {
        confirmed = true
        withAnimation(.easeOut(duration: 0.1)) { fill = 1 }
        NSHapticFeedbackManager.defaultPerformer.perform(.levelChange, performanceTime: .now)
        Task { @MainActor in
            // Long enough to see (and feel) that it took. The decision is
            // bound to this request, so nothing else can be answered meanwhile.
            try? await Task.sleep(for: .milliseconds(240))
            onAlways()
        }
    }
}

private extension View {
    /// VoiceOver can't hold a button: "Always allow" is an action of its own.
    @ViewBuilder
    func alwaysAllowAction(_ action: (() -> Void)?) -> some View {
        if let action {
            accessibilityAction(named: Text("Always allow"), action)
        } else {
            self
        }
    }
}
