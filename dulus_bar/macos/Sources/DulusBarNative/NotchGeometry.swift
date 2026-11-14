import AppKit

struct NotchMetrics: Equatable {
    let width: CGFloat
    let height: CGFloat
    let screenFrame: CGRect

    var frame: CGRect {
        CGRect(
            x: screenFrame.midX - width / 2,
            y: screenFrame.maxY - height,
            width: width,
            height: height
        )
    }

    static func detect(on screen: NSScreen) -> NotchMetrics {
        let left = screen.auxiliaryTopLeftArea?.width
        let right = screen.auxiliaryTopRightArea?.width
        let exactWidth: CGFloat?
        if let left, let right { exactWidth = screen.frame.width - left - right }
        else { exactWidth = nil }
        let height = screen.safeAreaInsets.top
        return NotchMetrics(
            width: max(exactWidth ?? 190, 150),
            height: max(height, 28),
            screenFrame: screen.frame
        )
    }
}
