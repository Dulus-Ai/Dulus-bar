import AppKit
import SwiftUI

final class IslandPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// The island never becomes key (it must never take your typing), so a
/// click on it must not be spent on focusing it first: take the first one.
final class FirstClickHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

@MainActor
final class PanelController {
    /// A transparent canvas from the physical top edge, roomy enough for the
    /// tallest card and its shadow. Only the black island ever paints, and
    /// the canvas lets every click through unless the pointer is on the card.
    static let canvasSize = CGSize(width: 560, height: 520)

    private let panel: IslandPanel
    private let model: IslandModel
    private let screen: ScreenModel
    private var hoverTimer: Timer?
    private var screenObserver: NSObjectProtocol?
    private var wasInside = false
    private var hoverEnterTime: Date?
    private let dwellInterval: TimeInterval = 0.35 // 350ms dwell required before expanding

    init(model: IslandModel, screen: ScreenModel) {
        self.model = model
        self.screen = screen
        panel = IslandPanel(
            contentRect: PanelController.canvasFrame(for: screen.metrics),
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
        panel.contentView = FirstClickHostingView(rootView: IslandView(model: model, screen: screen))
        panel.orderFrontRegardless()
        installMouseTracking()
        // A display plugged in or out, a resolution change, the notch screen
        // closed: re-anchor instead of staying where the old screen was.
        screenObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.screensChanged() }
        }
    }

    private static func canvasFrame(for metrics: NotchMetrics) -> CGRect {
        CGRect(
            x: metrics.screenFrame.midX - canvasSize.width / 2,
            y: metrics.screenFrame.maxY - canvasSize.height,
            width: canvasSize.width,
            height: canvasSize.height
        )
    }

    private func screensChanged() {
        guard let target = NotchMetrics.preferredScreen() else { return }
        let metrics = NotchMetrics.detect(on: target)
        if metrics != screen.metrics { screen.metrics = metrics }
        panel.setFrame(Self.canvasFrame(for: metrics), display: true)
        panel.orderFrontRegardless()
    }

    private func installMouseTracking() {
        // Polling NSEvent.mouseLocation at 30 Hz without needing accessibility perms
        let timer = Timer(timeInterval: 1.0 / 30, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.updateHover() }
        }
        timer.tolerance = 0.01
        RunLoop.main.add(timer, forMode: .common)
        hoverTimer = timer
        updateHover()
    }

    private func updateHover() {
        let mouse = NSEvent.mouseLocation
        let metrics = screen.metrics
        // Collapsed trigger zone: strictly the top 10 points (or physical notch cutout)
        // without dipping downward into the browser tab bar.
        let triggerHeight = min(metrics.height, 10.0)
        let notchHit = CGRect(
            x: metrics.screenFrame.midX - metrics.width / 2,
            y: metrics.screenFrame.maxY - triggerHeight,
            width: metrics.width,
            height: triggerHeight
        )
        // The card exactly as the view laid it out, plus a little slack.
        let cardWidth = IslandLayout.width + 2 * IslandLayout.flare + 16
        let cardHeight = max(model.contentHeight, metrics.height) + 8
        let expandedHit = CGRect(
            x: metrics.screenFrame.midX - cardWidth / 2,
            y: metrics.screenFrame.maxY - cardHeight,
            width: cardWidth,
            height: cardHeight
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

        // A press that began on the island keeps it live until the button is
        // released, wherever the pointer goes: the press must see its own end
        // (dragging off Allow backs out of it).
        if !inside && wasInside && NSEvent.pressedMouseButtons & 1 == 1 { return }

        if inside != wasInside {
            wasInside = inside
            model.hoverExpanded = inside
            panel.ignoresMouseEvents = !inside
        }
    }
}
