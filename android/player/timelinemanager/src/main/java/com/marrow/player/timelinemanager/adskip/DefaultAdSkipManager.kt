package com.marrow.player.timelinemanager.adskip

class DefaultAdSkipManager(private val processor: TimelineMarkerProcessor) {

    fun isPrerollSkippable(positionMs: Long, isAdFreeTier: Boolean): Boolean {
        val marker = processor.markerAt(positionMs) ?: return false
        return marker.isSkippable || isAdFreeTier
    }

    fun markersForOverlay(): List<TimelineMarker> = processor.skippableMarkers()

    fun skipTarget(positionMs: Long): Long? = processor.markerAt(positionMs)?.endMs
}
