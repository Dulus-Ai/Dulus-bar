import Foundation

/// The Allow button's press rules: a click allows once; holding it (when the
/// agent offers it) always allows. No UI in here, so the rules that keep an
/// approval safe are testable:
///
/// * a hold that fired "always" never also counts as a click on release —
///   the release must not land on the next request in the queue;
/// * a press that wanders off the button counts for nothing (drag away to
///   back out, as with any Mac button);
/// * "always" only fires while the mouse button is really still down.
struct HoldGate {
    enum Outcome: Equatable { case none, allow, always }

    let canAlways: Bool
    private(set) var pressing = false
    private(set) var fired = false
    private(set) var abandoned = false

    init(canAlways: Bool) {
        self.canAlways = canAlways
    }

    /// The hold is live: pressed, still on the button, not fired yet.
    var holding: Bool { pressing && !abandoned && !fired }

    mutating func press() {
        guard !pressing else { return }
        pressing = true
        fired = false
        abandoned = false
    }

    /// The pointer left the button mid-press.
    mutating func leave() {
        if pressing && !fired { abandoned = true }
    }

    /// The hold timer ran out: "always", once, if the press is still on.
    mutating func holdElapsed(buttonDown: Bool) -> Outcome {
        guard canAlways, holding, buttonDown else { return .none }
        fired = true
        return .always
    }

    /// The mouse button came up (`inside`: over the button).
    mutating func release(inside: Bool) -> Outcome {
        defer {
            pressing = false
            abandoned = false
        }
        guard holding, inside else { return .none }
        return .allow
    }
}
