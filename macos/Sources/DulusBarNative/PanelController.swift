import AppKit
import SwiftUI

final class IslandPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class PanelController {
    private let panel: IslandPanel
    private let model: IslandModel
    private let notch: NotchMetrics
    private var hoverTimer: Timer?
    private var wasInside = false

    init(model: IslandModel) {
        self.model = model
        let screen = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 }) ?? NSScreen.main ?? NSScreen.screens[0]
        notch = NotchMetrics.detect(on: screen)

        // A transparent interaction canvas begins at the physical top edge.
        // Only the black SwiftUI shape paints; the rest never obstructs content.
        let panelSize = CGSize(width: 430, height: 190)
        let origin = CGPoint(
            x: screen.frame.midX - panelSize.width / 2,
            y: screen.frame.maxY - panelSize.height
        )
        panel = IslandPanel(
            contentRect: CGRect(origin: origin, size: panelSize),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .screenSaver
        panel.collectionBehavior = [
            .canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle,
        ]
        panel.hidesOnDeactivate = false
        panel.isMovable = false
        panel.isMovableByWindowBackground = false
        panel.ignoresMouseEvents = true
        panel.contentView = NSHostingView(rootView: IslandView(model: model, notch: notch))
        panel.orderFrontRegardless()
        installMouseTracking()
    }

    deinit {
        hoverTimer?.invalidate()
    }

    private func installMouseTracking() {
        // Polling NSEvent.mouseLocation needs no Accessibility/Input Monitoring
        // permission, unlike a global event tap. 20 Hz feels immediate and is tiny.
        hoverTimer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.updateHover() }
        }
        updateHover()
    }

    private func updateHover() {
        let mouse = NSEvent.mouseLocation
        // Include a forgiving shoulder around the real cutout so entering it
        // feels effortless; when expanded, retain hover over the dropped bubble.
        let notchHit = notch.frame.insetBy(dx: -16, dy: -7)
        let visibleHeight: CGFloat = model.expanded ? (model.permission == nil ? 94 : 142) : notch.height
        let expandedHit = CGRect(
            x: notch.screenFrame.midX - 180,
            y: notch.screenFrame.maxY - visibleHeight - 8,
            width: 360,
            height: visibleHeight + 16
        )
        let inside = notchHit.contains(mouse) || (model.expanded && expandedHit.contains(mouse))
        if inside != wasInside {
            wasInside = inside
            model.hoverExpanded = inside
            panel.ignoresMouseEvents = !inside
        }
    }
}
