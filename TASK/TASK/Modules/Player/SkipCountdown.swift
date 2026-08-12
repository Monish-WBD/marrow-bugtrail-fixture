import Foundation

/// State surfaced to the UI while the viewer sits inside an ad marker.
///
/// The UI is expected to render "Skip in Ns" while `isSkipReady` is false and
/// flip to an enabled "Skip" button once `isSkipReady` becomes true.
public struct SkipCountdownState: Equatable {
    public let secondsUntilSkip: Int
    public let isSkipReady: Bool

    public init(secondsUntilSkip: Int, isSkipReady: Bool) {
        self.secondsUntilSkip = secondsUntilSkip
        self.isSkipReady = isSkipReady
    }

    public static let notApplicable = SkipCountdownState(secondsUntilSkip: 0, isSkipReady: false)
}

/// Computes the skip countdown for a viewer currently inside `marker`.
///
/// The viewer becomes skip-eligible `AdBreakPolicy.skipAvailableAfter` seconds
/// after the marker's start; before then the UI renders a rounded-up
/// "Skip in Ns".
public final class SkipCountdown {
    private let policy: AdBreakPolicy

    public init(policy: AdBreakPolicy = .default) {
        self.policy = policy
    }

    public func state(for marker: TimelineMarker, at position: TimeInterval) -> SkipCountdownState {
        let skipReadyAt = marker.startTime + policy.skipAvailableAfter
        let remaining = skipReadyAt - position
        if remaining <= 0 {
            return SkipCountdownState(secondsUntilSkip: 0, isSkipReady: true)
        }
        let secondsRemaining = Int(ceil(remaining))
        return SkipCountdownState(secondsUntilSkip: max(1, secondsRemaining), isSkipReady: false)
    }
}
