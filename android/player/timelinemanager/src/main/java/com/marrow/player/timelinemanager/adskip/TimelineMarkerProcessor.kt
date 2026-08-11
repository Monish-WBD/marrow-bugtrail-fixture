package com.marrow.player.timelinemanager.adskip

data class TimelineMarker(
    val startMs: Long,
    val endMs: Long,
    val isSkippable: Boolean,
    val isPreroll: Boolean
)

class TimelineMarkerProcessor(private val markers: List<TimelineMarker> = emptyList()) {

    fun markerAt(positionMs: Long): TimelineMarker? {
        println("TimelineMarkerProcessor.markerAt called")
        return markers.firstOrNull { positionMs >= it.startMs && positionMs < it.endMs }
    }

    fun skippableMarkers(): List<TimelineMarker> = markers.filter { it.isSkippable }
}
