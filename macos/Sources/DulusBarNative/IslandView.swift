import SwiftUI

struct IslandView: View {
    @ObservedObject var model: IslandModel
    let notch: NotchMetrics

    private var width: CGFloat { model.expanded ? 344 : notch.width }
    private var height: CGFloat { model.expanded ? (model.permission == nil ? 94 : 142) : notch.height }

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .top) {
                RoundedRectangle(cornerRadius: model.expanded ? 28 : 15, style: .continuous)
                    .fill(.black)
                    .frame(width: width, height: height)
                    .shadow(color: .black.opacity(model.expanded ? 0.34 : 0), radius: 18, y: 8)

                if model.expanded {
                    expandedContent
                        .frame(width: width, height: height)
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
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Dulus Bar")
    }

    @ViewBuilder
    private var expandedContent: some View {
        VStack(spacing: 10) {
            if let session = model.foremost {
                HStack(spacing: 10) {
                    statusOrb(session.status)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            Text(session.agent)
                                .font(.system(size: 14, weight: .semibold, design: .rounded))
                                .foregroundStyle(.white)
                            if !session.model.isEmpty {
                                Text(session.model)
                                    .font(.system(size: 10, weight: .medium))
                                    .foregroundStyle(.white.opacity(0.48))
                                    .lineLimit(1)
                            }
                        }
                        Text(session.message.isEmpty ? session.status : session.message)
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

            if model.permission != nil {
                HStack(spacing: 9) {
                    Button("Deny") { model.decide(false) }
                        .buttonStyle(IslandButtonStyle(primary: false))
                        .accessibilityLabel("Deny agent permission")
                    Button("Allow") { model.decide(true) }
                        .buttonStyle(IslandButtonStyle(primary: true))
                        .accessibilityLabel("Allow agent permission")
                }
            }
        }
        .padding(.top, notch.height + 9)
        .padding(.horizontal, 18)
        .padding(.bottom, 12)
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
