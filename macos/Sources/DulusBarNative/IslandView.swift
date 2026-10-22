import SwiftUI

struct IslandView: View {
    @ObservedObject var model: IslandModel
    let notch: NotchMetrics

    static let expandedWidth: CGFloat = 372

    private var width: CGFloat { model.expanded ? Self.expandedWidth : notch.width }
    private var height: CGFloat { model.cardHeight(notchHeight: notch.height) }

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .top) {
                RoundedRectangle(cornerRadius: model.expanded ? 28 : 15, style: .continuous)
                    .fill(.black)
                    .frame(width: width, height: height)
                    .shadow(color: .black.opacity(model.expanded ? 0.34 : 0), radius: 18, y: 8)

                if model.expanded {
                    expandedContent
                        .frame(width: width, height: height, alignment: .top)
                        .transition(.opacity.combined(with: .scale(scale: 0.96, anchor: .top)))
                }
            }
            .frame(maxWidth: .infinity, alignment: .top)
            .contentShape(Rectangle())
            .onTapGesture { model.eventExpanded.toggle() }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .animation(.spring(response: 0.42, dampingFraction: 0.82, blendDuration: 0.12), value: model.expanded)
        .animation(.spring(response: 0.42, dampingFraction: 0.82), value: width)
        .animation(.spring(response: 0.42, dampingFraction: 0.82), value: height)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Dulus Bar")
    }

    @ViewBuilder
    private var expandedContent: some View {
        VStack(spacing: 10) {
            if let session = model.foremost {
                HStack(spacing: 10) {
                    Image("dulus-bird", bundle: .module)
                        .resizable()
                        .scaledToFit()
                        .frame(width: 28, height: 28)
                        .accessibilityHidden(true)
                    statusOrb(session.status)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            Text(session.agent)
                                .font(.system(size: 14, weight: .semibold, design: .rounded))
                                .foregroundStyle(.white)
                            if !session.title.isEmpty {
                                Text(session.title)
                                    .font(.system(size: 11, weight: .medium, design: .rounded))
                                    .foregroundStyle(.white.opacity(0.42))
                                    .lineLimit(1)
                            }
                            if !session.model.isEmpty {
                                Text(session.model)
                                    .font(.system(size: 10, weight: .medium))
                                    .foregroundStyle(.white.opacity(0.48))
                                    .lineLimit(1)
                            }
                        }
                        // While a request is up the card below says what it is.
                        Text(model.permission != nil ? "needs your approval"
                             : (session.message.isEmpty ? session.status : session.message))
                            .font(.system(size: 11, weight: .regular))
                            .foregroundStyle(.white.opacity(0.62))
                            .lineLimit(1)
                    }
                    Spacer(minLength: 4)
                    if !session.context.isEmpty {
                        Text("ctx \(session.context)")
                            .font(.system(size: 10, weight: .medium, design: .rounded))
                            .foregroundStyle(.white.opacity(0.46))
                    }
                }
            } else {
                HStack(spacing: 9) {
                    Image(systemName: "sparkles")
                        .foregroundStyle(Color(red: 1, green: 0.48, blue: 0.12))
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Dulus Bar")
                            .font(.system(size: 14, weight: .semibold, design: .rounded))
                            .foregroundStyle(.white)
                        Text(model.connected ? "Ready for your agents" : "Connecting…")
                            .font(.system(size: 11))
                            .foregroundStyle(.white.opacity(0.55))
                    }
                    Spacer()
                }
            }

            if let request = model.permission {
                permissionCard(request)
            }
        }
        .padding(.top, notch.height + 9)
        .padding(.horizontal, 18)
        .padding(.bottom, 12)
    }

    @ViewBuilder
    private func permissionCard(_ request: PermissionRequest) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(request.tool.isEmpty ? "Run a tool?" : request.tool)
                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.9))
                    .lineLimit(1)
                Spacer(minLength: 4)
                if model.permissions.count > 1 {
                    Text("1 of \(model.permissions.count)")
                        .font(.system(size: 9, weight: .bold, design: .rounded))
                        .foregroundStyle(.black)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(Capsule().fill(Color.orange))
                        .accessibilityLabel("\(model.permissions.count) requests waiting")
                }
            }
            if !request.detailLine.isEmpty {
                Text(request.detailLine)
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(.white.opacity(0.78))
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 5)
                    .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Color.white.opacity(0.08)))
            }
        }
        HStack(spacing: 9) {
            Button("Deny") { model.decide(false) }
                .buttonStyle(IslandButtonStyle(primary: false))
                .accessibilityLabel("Deny agent permission")
            if request.canAlways {
                Button("Always") { model.decide(true, always: true) }
                    .buttonStyle(IslandButtonStyle(primary: false))
                    .help(request.alwaysLabel.isEmpty ? "Don't ask again" : "Don't ask again for \(request.alwaysLabel)")
                    .accessibilityLabel("Always allow")
            }
            Button("Allow") { model.decide(true) }
                .buttonStyle(IslandButtonStyle(primary: true))
                .accessibilityLabel("Allow agent permission")
        }
    }

    private func statusOrb(_ status: String) -> some View {
        let color: Color = switch status {
        case "waiting": .orange
        case "error": .red
        case "done": .green
        default: Color(red: 1, green: 0.48, blue: 0.12)
        }
        return Circle()
            .fill(color)
            .frame(width: 8, height: 8)
            .shadow(color: color.opacity(0.65), radius: 4)
            .accessibilityLabel(status)
    }
}

private struct IslandButtonStyle: ButtonStyle {
    let primary: Bool
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: .semibold, design: .rounded))
            .foregroundStyle(primary ? Color.black : Color.white.opacity(0.82))
            .frame(maxWidth: .infinity)
            .frame(height: 30)
            .background(primary ? Color.white : Color.white.opacity(0.12))
            .clipShape(Capsule())
            .opacity(configuration.isPressed ? 0.72 : 1)
    }
}
