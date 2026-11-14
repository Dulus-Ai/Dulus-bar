import SwiftUI

/// The island. Collapsed it *is* the notch (a drawn one on Macs without),
/// with one small light per live agent on a ledge underneath. Open, the same
/// black shape pours out of the notch into a card: the session list, or the
/// request waiting for you.
struct IslandView: View {
    @ObservedObject var model: IslandModel
    @ObservedObject var screen: ScreenModel

    var body: some View {
        let notch = screen.metrics
        let expanded = model.expanded
        let lights = expanded ? [] : model.lights
        let chin = lights.isEmpty ? 0 : IslandLayout.chinHeight
        let size = expanded
            ? CGSize(width: IslandLayout.width + 2 * IslandLayout.flare, height: max(model.contentHeight, notch.height))
            : CGSize(width: notch.width, height: notch.height + chin)
        let shape = IslandShape(
            flare: expanded ? IslandLayout.flare : (notch.hasNotch ? 0 : 6),
            radius: expanded ? IslandLayout.radius : 9,
            chinWidth: lights.isEmpty ? 0 : CGFloat(lights.count) * 9 + 14,
            chinHeight: chin
        )

        VStack(spacing: 0) {
            ZStack(alignment: .top) {
                // Always laid out, so its height is known before it opens.
                ExpandedContent(model: model, notch: notch)
                    .frame(width: IslandLayout.width)
                    .fixedSize(horizontal: false, vertical: true)
                    .background(GeometryReader { proxy in
                        Color.clear.preference(key: ContentHeightKey.self, value: proxy.size.height)
                    })
                    .opacity(expanded ? 1 : 0)
                    .allowsHitTesting(expanded)
                if !lights.isEmpty {
                    LightsRow(sessions: lights)
                        .frame(height: chin)
                        .padding(.top, notch.height)
                        .transition(.opacity)
                }
            }
            .frame(width: size.width, height: size.height, alignment: .top)
            .clipShape(shape)
            .background(
                shape
                    .fill(Color.black)
                    .shadow(color: .black.opacity(expanded ? 0.38 : 0), radius: 18, y: 8)
            )
            .contentShape(shape)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .onPreferenceChange(ContentHeightKey.self) { [model] height in
            Task { @MainActor in
                if abs(model.contentHeight - height) > 0.5 { model.contentHeight = height }
            }
        }
        .animation(.spring(response: 0.4, dampingFraction: 0.82), value: expanded)
        .animation(.spring(response: 0.38, dampingFraction: 0.86), value: size)
        .animation(.easeOut(duration: 0.2), value: lights.map(\.id))
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text("Dulus Bar"))
    }
}

private struct ContentHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

/// One light per live agent: green working, amber waiting for you, red failed.
private struct LightsRow: View {
    let sessions: [Session]

    var body: some View {
        HStack(spacing: 4) {
            ForEach(sessions) { session in
                StatusLight(status: session.status, size: 5)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(verbatim: "\(sessions.count) agents active"))
    }
}

private struct ExpandedContent: View {
    @ObservedObject var model: IslandModel
    let notch: NotchMetrics

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Header(model: model, notch: notch)
                .frame(height: notch.height)
            if let request = model.permission {
                PermissionCard(
                    request: request,
                    session: model.sessions[request.sessionKey],
                    queued: model.permissions.count
                ) { approved, always in
                    model.decide(approved, always: always, requestID: request.requestID)
                }
                // A new request is a new card: its buttons arm afresh.
                .id(request.requestID)
                .transition(.opacity)
            } else if model.sessions.isEmpty {
                EmptyState(connected: model.connected)
            } else {
                SessionList(model: model)
            }
        }
        .padding(.horizontal, IslandLayout.padding)
        .padding(.bottom, 14)
        .animation(.easeOut(duration: 0.2), value: model.permission?.requestID)
    }
}

/// The strip beside the notch: the brand on the left, the gist on the right.
private struct Header: View {
    @ObservedObject var model: IslandModel
    let notch: NotchMetrics

    var body: some View {
        let wing = max(0, (IslandLayout.width - 2 * IslandLayout.padding - notch.width) / 2 - 6)
        HStack(spacing: 0) {
            HStack(spacing: 6) {
                Image("dulus-bird", bundle: .module)
                    .resizable()
                    .scaledToFit()
                    .frame(width: 15, height: 15)
                Text("Dulus Bar")
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.72))
            }
            .lineLimit(1)
            .minimumScaleFactor(0.8)
            .frame(width: wing, alignment: .leading)
            Spacer(minLength: 0)
            Text(verbatim: summary.text)
                .font(.system(size: 10.5, weight: .semibold, design: .rounded))
                .foregroundStyle(summary.color)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(width: wing, alignment: .trailing)
        }
    }

    @MainActor
    private var summary: (text: String, color: Color) {
        if !model.connected { return ("Connecting…", Color.white.opacity(0.45)) }
        if !model.permissions.isEmpty { return ("\(model.permissions.count) waiting", Palette.waiting) }
        let active = model.sessions.values.filter(\.isActive).count
        if active > 0 { return ("\(active) running", Palette.running) }
        return ("Ready", Color.white.opacity(0.45))
    }
}

private struct EmptyState: View {
    let connected: Bool

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: connected ? "sparkles" : "bolt.horizontal.circle")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(connected ? Palette.brand : Color.white.opacity(0.5))
                .frame(width: 28, height: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(connected ? "Ready for your agents" : "Connecting to the hub…")
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.white)
                Text(connected ? "Start Claude Code or any wrapped agent and it shows up here."
                               : "The island starts its hub on its own; give it a second.")
                    .font(.system(size: 11))
                    .foregroundStyle(Color.white.opacity(0.5))
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 2)
    }
}

private struct SessionList: View {
    @ObservedObject var model: IslandModel

    var body: some View {
        let rows = model.rows(limit: IslandLayout.maxRows)
        let peeking = !model.hoverExpanded && model.spotlight != nil
        let more = peeking ? 0 : max(0, model.sessions.count - rows.count)
        VStack(spacing: 2) {
            ForEach(rows) { session in
                SessionRow(session: session, canFocus: model.localPID(of: session) != nil) {
                    model.focus(session)
                }
            }
            if more > 0 {
                Text(verbatim: "+\(more) more")
                    .font(.system(size: 10.5, weight: .medium, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.4))
                    .frame(maxWidth: .infinity)
                    .padding(.top, 2)
            }
        }
    }
}

/// An agent session: who, where, what it's doing, model · context, how long.
/// Click it to bring up the terminal or editor it runs in.
private struct SessionRow: View {
    let session: Session
    let canFocus: Bool
    let onTap: () -> Void
    @State private var hovering = false

    var body: some View {
        let style = AgentStyle.of(session.agent)
        HStack(spacing: 10) {
            AgentBadge(style: style, status: session.status)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(verbatim: style.display)
                        .font(.system(size: 13, weight: .semibold, design: .rounded))
                        .foregroundStyle(Color.white)
                        .layoutPriority(1)
                    if !session.title.isEmpty {
                        Text(verbatim: session.title)
                            .font(.system(size: 11, weight: .medium, design: .rounded))
                            .foregroundStyle(Color.white.opacity(0.42))
                    }
                    Spacer(minLength: 4)
                    MetaText(session: session, style: style)
                        .layoutPriority(1)
                }
                .lineLimit(1)
                HStack(spacing: 6) {
                    Text(verbatim: session.message.isEmpty ? session.status.capitalized : session.message)
                        .font(.system(size: 11))
                        .foregroundStyle(Color.white.opacity(0.62))
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Spacer(minLength: 4)
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        Text(verbatim: shortDuration(context.date.timeIntervalSince(session.statusSince)))
                            .font(.system(size: 10, weight: .medium, design: .rounded).monospacedDigit())
                            .foregroundStyle(Color.white.opacity(0.34))
                    }
                }
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color.white.opacity(hovering ? 0.08 : 0))
        )
        .contentShape(Rectangle())
        .onHover { inside in hovering = inside && canFocus }
        .onTapGesture { if canFocus { onTap() } }
        .help(canFocus ? "Show the terminal it runs in" : "")
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(canFocus ? .isButton : [])
    }
}

/// "opus 4.5 · ctx 38%": the model in the agent's color, the context by how full.
private struct MetaText: View {
    let session: Session
    let style: AgentStyle

    var body: some View {
        HStack(spacing: 4) {
            if !session.model.isEmpty {
                Text(verbatim: session.model).foregroundStyle(style.color)
            }
            if !session.model.isEmpty && !session.context.isEmpty {
                Text(verbatim: "·").foregroundStyle(Color.white.opacity(0.3))
            }
            if !session.context.isEmpty {
                Text(verbatim: "ctx \(session.context)").foregroundStyle(Palette.context(session.context))
            }
        }
        .font(.system(size: 10, weight: .medium, design: .rounded))
    }
}

/// The request on screen: who asks, for what (verbatim — the full command,
/// wrapped, never cut off sideways), and Deny / Allow (hold: always allow).
private struct PermissionCard: View {
    let request: PermissionRequest
    let session: Session?
    let queued: Int
    let onDecide: (_ approved: Bool, _ always: Bool) -> Void

    /// The buttons wake up this long after the card appears, so the second
    /// half of a double-click can't answer a request you haven't seen yet.
    static let armDelay: Duration = .milliseconds(350)

    @State private var armed = false

    var body: some View {
        let style = AgentStyle.of(request.agent)
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 9) {
                AgentBadge(style: style, status: "waiting", size: 26)
                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 6) {
                        Text(verbatim: style.display)
                            .font(.system(size: 13, weight: .semibold, design: .rounded))
                            .foregroundStyle(Color.white)
                        if let title = session?.title, !title.isEmpty {
                            Text(verbatim: title)
                                .font(.system(size: 11, weight: .medium, design: .rounded))
                                .foregroundStyle(Color.white.opacity(0.45))
                        }
                    }
                    .lineLimit(1)
                    Text(verbatim: subtitle)
                        .font(.system(size: 10.5, weight: .medium, design: .rounded))
                        .foregroundStyle(Palette.waiting.opacity(0.85))
                        .lineLimit(1)
                }
                Spacer(minLength: 6)
                if queued > 1 {
                    QueueBadge(position: 1, total: queued)
                }
            }
            Text(verbatim: request.title)
                .font(.system(size: 12.5, weight: .semibold, design: .rounded))
                .foregroundStyle(Color.white.opacity(0.92))
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            if !request.code.isEmpty {
                Text(verbatim: wrapCode(request.code, columns: IslandLayout.codeColumns, maxLines: IslandLayout.codeLines))
                    .font(.system(size: IslandLayout.codeFontSize, design: .monospaced))
                    .foregroundStyle(Color.white.opacity(0.86))
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, IslandLayout.codePadding)
                    .padding(.vertical, 7)
                    .background(
                        RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Color.white.opacity(0.07))
                    )
            }
            HStack(spacing: 8) {
                Button("Deny") { onDecide(false, false) }
                    .buttonStyle(IslandButtonStyle())
                    .disabled(!armed)
                HoldToAllowButton(
                    canAlways: request.canAlways,
                    alwaysLabel: request.alwaysText,
                    armed: armed,
                    onAllow: { onDecide(true, false) },
                    onAlways: { onDecide(true, true) }
                )
            }
            .opacity(armed ? 1 : 0.5)
            if request.canAlways {
                Text(verbatim: hint)
                    .font(.system(size: 10, weight: .medium, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.42))
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .frame(maxWidth: .infinity)
            }
        }
        .task {
            try? await Task.sleep(for: Self.armDelay)
            withAnimation(.easeOut(duration: 0.18)) { armed = true }
        }
    }

    private var subtitle: String {
        if let model = session?.model, !model.isEmpty { return "needs your approval · \(model)" }
        return "needs your approval"
    }

    private var hint: String {
        request.alwaysText.isEmpty
            ? "Hold Allow to always allow"
            : "Hold Allow to always allow · \(request.alwaysText)"
    }
}
