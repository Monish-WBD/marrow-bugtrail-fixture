import Foundation

/// Playback tiers understood by the ad break subsystem.
public enum SubscriptionTier: String, Equatable {
    case free
    case standard
    case premium
    case adFree
}

/// Configures how the scheduler prefetches and displays ad breaks.
///
/// The defaults here match the values shipped in the 2026.08 client rollout;
/// any change to a default is expected to go through the ads platform team.
public struct AdBreakPolicy: Equatable {
    /// How far ahead of the marker start we begin prefetching creatives.
    public let prefetchLeadTime: TimeInterval
    /// Minimum spacing between two consecutive prefetches, to avoid stampedes
    /// when several short markers sit close together.
    public let minimumPrefetchInterval: TimeInterval
    /// If the viewer scrubs backwards, only re-arm the break when the delta is
    /// larger than this threshold. Small backwards nudges (frame-accurate seeks)
    /// should not fire another prefetch.
    public let backwardSeekReArmThreshold: TimeInterval
    /// Tiers that are allowed to skip mid-roll markers even when the marker
    /// itself is not flagged skippable. Preroll is handled separately.
    public let midrollSkipAllowedTiers: Set<SubscriptionTier>

    public init(
        prefetchLeadTime: TimeInterval = 6.0,
        minimumPrefetchInterval: TimeInterval = 2.0,
        backwardSeekReArmThreshold: TimeInterval = 1.5,
        midrollSkipAllowedTiers: Set<SubscriptionTier> = [.premium, .adFree]
    ) {
        self.prefetchLeadTime = prefetchLeadTime
        self.minimumPrefetchInterval = minimumPrefetchInterval
        self.backwardSeekReArmThreshold = backwardSeekReArmThreshold
        self.midrollSkipAllowedTiers = midrollSkipAllowedTiers
    }

    public static let `default` = AdBreakPolicy()
}
