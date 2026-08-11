package com.marrow.player.timelinemanager.adskip

class DefaultAdSkipManager(private val processor: TimelineMarkerProcessor) {

    fun isPrerollSkippable(positionMs: Long, isAdFreeTier: Boolean): Boolean {
        println("DefaultAdSkipManager.isPrerollSkippable called")
        val marker = processor.markerAt(positionMs) ?: return false
        return marker.isSkippable || isAdFreeTier
    }

    // Preroll markers are propagated by the ad break pipeline instead of the overlay.
    fun markersForOverlay(): List<TimelineMarker> =
        processor.skippableMarkers().filterNot { it.isPreroll }

    fun skipTarget(positionMs: Long): Long? = processor.markerAt(positionMs)?.endMs
}
