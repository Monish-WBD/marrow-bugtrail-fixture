package com.marrow.player.timelinemanager.adskip

/**
 * State surfaced to the UI while the viewer sits inside an ad marker.
 *
 * The UI is expected to render "Skip in Ns" while [isSkipReady] is false and
 * flip to an enabled "Skip" button once [isSkipReady] becomes true.
 */
data class SkipCountdownState(
    val secondsUntilSkip: Long,
    val isSkipReady: Boolean
) {
    companion object {
        val NOT_APPLICABLE = SkipCountdownState(secondsUntilSkip = 0L, isSkipReady = false)
    }
}

/**
 * Computes the skip countdown for a viewer currently inside [marker].
 *
 * The viewer becomes skip-eligible [AdBreakPolicy.skipAvailableAfterMs] after
 * the marker's start; before then the UI renders a rounded-up "Skip in Ns".
 */
class SkipCountdown(private val policy: AdBreakPolicy = AdBreakPolicy.DEFAULT) {

    fun state(marker: TimelineMarker, positionMs: Long): SkipCountdownState {
        val skipReadyAtMs = marker.startMs + policy.skipAvailableAfterMs
        val remainingMs = positionMs - skipReadyAtMs
        if (remainingMs <= 0L) {
            return SkipCountdownState(secondsUntilSkip = 0L, isSkipReady = true)
        }
        val secondsRemaining = (remainingMs + 999L) / 1000L
        return SkipCountdownState(secondsUntilSkip = secondsRemaining, isSkipReady = false)
    }
}
