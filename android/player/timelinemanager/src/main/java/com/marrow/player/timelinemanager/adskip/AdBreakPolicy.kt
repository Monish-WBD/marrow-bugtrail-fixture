package com.marrow.player.timelinemanager.adskip

/**
 * Playback tiers understood by the ad break subsystem.
 */
enum class SubscriptionTier {
    FREE,
    STANDARD,
    PREMIUM,
    AD_FREE
}

/**
 * Configures how the scheduler prefetches and displays ad breaks.
 *
 * Defaults mirror the values shipped in the 2026.08 client rollout; keep the
 * iOS and Android policies in step when tuning.
 */
data class AdBreakPolicy(
    /** How far ahead of the marker start we begin prefetching creatives. */
    val prefetchLeadTimeMs: Long = 6_000L,
    /**
     * Minimum spacing between two consecutive prefetches, to avoid stampedes
     * when several short markers sit close together.
     */
    val minimumPrefetchIntervalMs: Long = 2_000L,
    /**
     * If the viewer scrubs backwards, only re-arm the break when the delta is
     * larger than this threshold. Small backwards nudges (frame-accurate seeks)
     * should not fire another prefetch.
     */
    val backwardSeekReArmThresholdMs: Long = 1_500L,
    /**
     * How long after a marker begins the viewer becomes eligible to skip it,
     * assuming their tier is entitled. The UI surfaces a "Skip in Ns" countdown
     * during this window.
     */
    val skipAvailableAfterMs: Long = 5_000L,
    /**
     * Tiers that are allowed to skip mid-roll markers even when the marker
     * itself is not flagged skippable. Preroll is handled separately.
     */
    val midrollSkipAllowedTiers: Set<SubscriptionTier> = setOf(
        SubscriptionTier.PREMIUM,
        SubscriptionTier.AD_FREE
    )
) {
    companion object {
        val DEFAULT = AdBreakPolicy()
    }
}
