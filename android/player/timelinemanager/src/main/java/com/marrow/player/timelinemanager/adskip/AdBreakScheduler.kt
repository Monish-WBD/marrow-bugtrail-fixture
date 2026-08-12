package com.marrow.player.timelinemanager.adskip

/**
 * Reasons the scheduler may decide to arm (or skip arming) an upcoming break.
 */
sealed class AdBreakDecision {
    data class Prefetch(val markerStartMs: Long) : AdBreakDecision()
    object SkipEntitled : AdBreakDecision()
    data class Suppressed(val reason: SuppressReason) : AdBreakDecision()

    enum class SuppressReason {
        NOT_YET,
        TOO_SOON_AFTER_PREVIOUS,
        BACKWARD_SEEK_WITHIN_THRESHOLD,
        ALREADY_INSIDE_MARKER
    }
}

/**
 * Coordinates prefetching and skip eligibility for upcoming ad breaks.
 *
 * The scheduler is deliberately position-driven: the player calls
 * [evaluate] on every progress tick and the scheduler decides what, if
 * anything, should happen next. It intentionally does not own a timer of
 * its own so it stays deterministic under tests.
 */
class AdBreakScheduler(
    private val processor: TimelineMarkerProcessor,
    private val policy: AdBreakPolicy = AdBreakPolicy.DEFAULT,
    private val clock: () -> Long = { System.currentTimeMillis() }
) {
    private var lastEvaluatedPositionMs: Long? = null
    private var lastPrefetchedMarkerStartMs: Long? = null
    private var lastPrefetchWallClockMs: Long? = null

    /**
     * Called by the player for each progress tick.
     *
     * @param positionMs current playhead position in milliseconds
     * @param tier subscription tier of the current viewer
     * @return the decision the player should act on
     */
    fun evaluate(positionMs: Long, tier: SubscriptionTier): AdBreakDecision {
        val decision = decide(positionMs, tier)
        lastEvaluatedPositionMs = positionMs
        return decision
    }

    /**
     * Resets any latched state, e.g. when a new asset is loaded.
     */
    fun reset() {
        lastEvaluatedPositionMs = null
        lastPrefetchedMarkerStartMs = null
        lastPrefetchWallClockMs = null
    }

    private fun decide(positionMs: Long, tier: SubscriptionTier): AdBreakDecision {
        val current = processor.markerAt(positionMs)
        if (current != null) {
            if (!current.isPreroll && policy.midrollSkipAllowedTiers.contains(tier)) {
                return AdBreakDecision.SkipEntitled
            }
            return AdBreakDecision.Suppressed(AdBreakDecision.SuppressReason.ALREADY_INSIDE_MARKER)
        }

        val upcoming = nextMarker(positionMs)
            ?: return AdBreakDecision.Suppressed(AdBreakDecision.SuppressReason.NOT_YET)

        val distance = upcoming.startMs - positionMs
        if (distance > policy.prefetchLeadTimeMs) {
            return AdBreakDecision.Suppressed(AdBreakDecision.SuppressReason.NOT_YET)
        }

        val previous = lastEvaluatedPositionMs
        if (previous != null && positionMs < previous &&
            (previous - positionMs) < policy.backwardSeekReArmThresholdMs
        ) {
            return AdBreakDecision.Suppressed(AdBreakDecision.SuppressReason.BACKWARD_SEEK_WITHIN_THRESHOLD)
        }

        val lastStart = lastPrefetchedMarkerStartMs
        val lastWallClock = lastPrefetchWallClockMs
        if (lastStart == upcoming.startMs && lastWallClock != null &&
            (clock() - lastWallClock) < policy.minimumPrefetchIntervalMs
        ) {
            return AdBreakDecision.Suppressed(AdBreakDecision.SuppressReason.TOO_SOON_AFTER_PREVIOUS)
        }

        lastPrefetchedMarkerStartMs = upcoming.startMs
        lastPrefetchWallClockMs = clock()
        return AdBreakDecision.Prefetch(upcoming.startMs)
    }

    private fun nextMarker(positionMs: Long): TimelineMarker? =
        processor.upcomingMarkers(positionMs).firstOrNull()
}

/**
 * Markers whose [TimelineMarker.startMs] is strictly greater than [positionMs],
 * ordered by start time. Kept as a module-private helper so the scheduler
 * stays independent of storage order.
 */
internal fun TimelineMarkerProcessor.upcomingMarkers(positionMs: Long): List<TimelineMarker> =
    allMarkersInternal()
        .filter { it.startMs > positionMs }
        .sortedBy { it.startMs }

/**
 * Reflection-free accessor for the processor's marker list. The processor
 * keeps the underlying list private; we expose it here only for scheduler
 * use inside this package.
 */
@Suppress("UNCHECKED_CAST")
internal fun TimelineMarkerProcessor.allMarkersInternal(): List<TimelineMarker> {
    val field = this::class.java.getDeclaredField("markers")
    field.isAccessible = true
    return (field.get(this) as? List<TimelineMarker>) ?: emptyList()
}
