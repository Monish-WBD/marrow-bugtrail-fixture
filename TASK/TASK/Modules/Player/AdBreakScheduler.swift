import Foundation

/// Reasons the scheduler may decide to arm (or skip arming) an upcoming break.
public enum AdBreakDecision: Equatable {
    case prefetch(markerStart: TimeInterval)
    case skipEntitled
    case suppressed(reason: SuppressReason)

    public enum SuppressReason: String, Equatable {
        case notYet
        case tooSoonAfterPrevious
        case backwardSeekWithinThreshold
        case alreadyInsideMarker
    }
}

/// Coordinates prefetching and skip eligibility for upcoming ad breaks.
///
/// The scheduler is deliberately position-driven: the player calls
/// `evaluate(at:tier:)` on every progress tick and the scheduler decides
/// what, if anything, should happen next. It intentionally does not own a
/// timer of its own so it stays deterministic under tests.
public final class AdBreakScheduler {
    private let markers: TimelineMarkers
    private let policy: AdBreakPolicy

    private var lastEvaluatedPosition: TimeInterval?
    private var lastPrefetchedMarkerStart: TimeInterval?
    private var lastPrefetchWallClock: Date?
    private let clock: () -> Date

    public init(
        markers: TimelineMarkers,
        policy: AdBreakPolicy = .default,
        clock: @escaping () -> Date = Date.init
    ) {
        self.markers = markers
        self.policy = policy
        self.clock = clock
    }

    /// Called by the player for each progress tick.
    /// - Parameters:
    ///   - position: Current playhead position in seconds.
    ///   - tier: Subscription tier of the current viewer.
    /// - Returns: The decision the player should act on.
    public func evaluate(at position: TimeInterval, tier: SubscriptionTier) -> AdBreakDecision {
        defer { lastEvaluatedPosition = position }

        if let current = markers.marker(at: position) {
            if !current.isPreroll, policy.midrollSkipAllowedTiers.contains(tier) {
                return .skipEntitled
            }
            return .suppressed(reason: .alreadyInsideMarker)
        }

        guard let upcoming = nextMarker(after: position) else {
            return .suppressed(reason: .notYet)
        }

        let distance = upcoming.startTime - position
        guard distance <= policy.prefetchLeadTime else {
            return .suppressed(reason: .notYet)
        }

        if let previous = lastEvaluatedPosition,
           position < previous,
           (previous - position) < policy.backwardSeekReArmThreshold {
            return .suppressed(reason: .backwardSeekWithinThreshold)
        }

        if lastPrefetchedMarkerStart == upcoming.startTime,
           let last = lastPrefetchWallClock,
           clock().timeIntervalSince(last) < policy.minimumPrefetchInterval {
            return .suppressed(reason: .tooSoonAfterPrevious)
        }

        lastPrefetchedMarkerStart = upcoming.startTime
        lastPrefetchWallClock = clock()
        return .prefetch(markerStart: upcoming.startTime)
    }

    /// Resets any latched state, e.g. when a new asset is loaded.
    public func reset() {
        lastEvaluatedPosition = nil
        lastPrefetchedMarkerStart = nil
        lastPrefetchWallClock = nil
    }

    private func nextMarker(after position: TimeInterval) -> TimelineMarker? {
        markers.upcomingMarkers(after: position).first
    }
}

extension TimelineMarkers {
    /// Markers whose start time is strictly greater than `position`, ordered by start time.
    /// Kept as an internal helper so the scheduler stays independent of storage order.
    func upcomingMarkers(after position: TimeInterval) -> [TimelineMarker] {
        allMarkers()
            .filter { $0.startTime > position }
            .sorted { $0.startTime < $1.startTime }
    }

    /// Exposes the underlying markers for internal consumers.
    /// Intentionally not public: only players in the module should reach in.
    func allMarkers() -> [TimelineMarker] {
        Mirror(reflecting: self)
            .children
            .compactMap { $0.value as? [TimelineMarker] }
            .first ?? []
    }
}
