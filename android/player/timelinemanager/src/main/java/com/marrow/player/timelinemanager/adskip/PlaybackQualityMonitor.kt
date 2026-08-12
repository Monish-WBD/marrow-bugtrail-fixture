package com.marrow.player.timelinemanager.adskip

/**
 * A single quality sample captured by the player.
 */
data class PlaybackQualitySample(
    val timestampMs: Long,
    val bitrateKbps: Int,
    val bufferedDurationMs: Long,
    val droppedFrames: Int
)

/**
 * Verdicts the monitor can hand back to the player after each sample.
 */
enum class QualityVerdict {
    HEALTHY,
    DEGRADED_BUFFER,
    DEGRADED_FRAMES,
    REBUFFER_IMMINENT
}

/**
 * Rolling quality monitor that emits verdicts based on a sliding window of
 * recent samples. It intentionally does no I/O — the caller decides how to
 * react to the verdict (downshift ABR, log an event, etc).
 */
class PlaybackQualityMonitor(
    private val thresholds: Thresholds = Thresholds.DEFAULT
) {
    /** Configurable thresholds. Tuned against the 2026.08 rollout metrics. */
    data class Thresholds(
        val minHealthyBufferedDurationMs: Long = 4_000L,
        val rebufferBufferedDurationMs: Long = 1_000L,
        val droppedFramesPerWindow: Int = 12,
        val windowSize: Int = 5
    ) {
        companion object {
            val DEFAULT = Thresholds()
        }
    }

    private val window: ArrayDeque<PlaybackQualitySample> = ArrayDeque()

    /**
     * Feeds a new sample into the sliding window and returns the current verdict.
     */
    fun record(sample: PlaybackQualitySample): QualityVerdict {
        window.addLast(sample)
        while (window.size > thresholds.windowSize) {
            window.removeFirst()
        }
        return currentVerdict()
    }

    /**
     * Returns the verdict for the current window without mutating state.
     */
    fun currentVerdict(): QualityVerdict {
        val latest = window.lastOrNull() ?: return QualityVerdict.HEALTHY

        if (latest.bufferedDurationMs <= thresholds.rebufferBufferedDurationMs) {
            return QualityVerdict.REBUFFER_IMMINENT
        }

        val droppedInWindow = window.sumOf { it.droppedFrames }
        if (droppedInWindow >= thresholds.droppedFramesPerWindow) {
            return QualityVerdict.DEGRADED_FRAMES
        }

        if (latest.bufferedDurationMs < thresholds.minHealthyBufferedDurationMs) {
            return QualityVerdict.DEGRADED_BUFFER
        }

        return QualityVerdict.HEALTHY
    }

    /**
     * Clears the window, e.g. when the player is torn down or switched to a new asset.
     */
    fun reset() {
        window.clear()
    }
}
