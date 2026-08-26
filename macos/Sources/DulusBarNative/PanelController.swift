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
    private var hoverEnterTime: Date?
    private let dwellInterval: TimeInterval = 0.35 // 350ms dwell required before expanding

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
        // Polling NSEvent.mouseLocation at 20 Hz without needing accessibility perms
        hoverTimer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.updateHover() }
        }
        updateHover()
    }

    private func updateHover() {
        let mouse = NSEvent.mouseLocation
        // Collapsed trigger zone: strictly the top 10 points (or physical notch cutout)
        // without dipping downward into the browser tab bar.
        let triggerHeight = min(notch.height, 10.0)
        let notchHit = CGRect(
            x: notch.screenFrame.midX - notch.width / 2,
            y: notch.screenFrame.maxY - triggerHeight,
            width: notch.width,
            height: triggerHeight
        )
        let visibleHeight: CGFloat = model.expanded ? (model.permission == nil ? 94 : 142) : notch.height
        let expandedHit = CGRect(
            x: notch.screenFrame.midX - 180,
            y: notch.screenFrame.maxY - visibleHeight - 8,
            width: 360,
            height: visibleHeight + 16
        )

        var inside = false
        if model.expanded {
            // Once expanded, keep open while mouse is hovering the expanded card or notch
            inside = expandedHit.contains(mouse) || notchHit.contains(mouse)
            hoverEnterTime = nil
        } else {
            // When collapsed, require continuous dwell inside the notch area
            if notchHit.contains(mouse) {
                if let entered = hoverEnterTime {
                    if Date().timeIntervalSince(entered) >= dwellInterval {
                        inside = true
                    }
                } else {
                    hoverEnterTime = Date()
                }
            } else {
                hoverEnterTime = nil
            }
        }

        if inside != wasInside {
            wasInside = inside
            model.hoverExpanded = inside
            panel.ignoresMouseEvents = !inside
        }
    }
}
