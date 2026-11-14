import Foundation
#if canImport(Combine)
import Combine
#endif
#if canImport(AppKit)
import AppKit
#endif

struct NotchMetrics: Equatable {
    let width: CGFloat
    let height: CGFloat
    let screenFrame: CGRect
    /// A real camera notch; otherwise the island draws one in the menu bar's
    /// empty middle, as tall as the menu bar.
    var hasNotch = false

    var frame: CGRect {
        CGRect(
            x: screenFrame.midX - width / 2,
            y: screenFrame.maxY - height,
            width: width,
            height: height
        )
    }
}

/// The screen the island lives on; replaced when displays change.
@MainActor
final class ScreenModel: ObservableObject {
    @Published var metrics: NotchMetrics

    init(metrics: NotchMetrics) {
        self.metrics = metrics
    }
}

#if canImport(AppKit)
extension NotchMetrics {
    @MainActor
    static func detect(on screen: NSScreen) -> NotchMetrics {
        if screen.safeAreaInsets.top > 0,
           let left = screen.auxiliaryTopLeftArea, let right = screen.auxiliaryTopRightArea {
            return NotchMetrics(
                width: max(screen.frame.width - left.width - right.width, 120),
                height: screen.safeAreaInsets.top,
                screenFrame: screen.frame,
                hasNotch: true
            )
        }
        // No notch: as tall as the menu bar (or its usual height when it's hidden).
        let menuBar = screen.frame.maxY - screen.visibleFrame.maxY
        return NotchMetrics(
            width: 184,
            height: max(menuBar > 0 ? menuBar : NSStatusBar.system.thickness, 22),
            screenFrame: screen.frame,
            hasNotch: false
        )
    }

    /// The display with the notch, else the one with the menu bar.
    @MainActor
    static func preferredScreen() -> NSScreen? {
        NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 }) ?? NSScreen.screens.first ?? NSScreen.main
    }
}
#endif
