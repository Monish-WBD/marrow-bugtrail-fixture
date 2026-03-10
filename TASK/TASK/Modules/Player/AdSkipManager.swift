import Foundation

/// Decides whether the current playback position sits inside a skippable section.
public final class AdSkipManager {
    private let markers: TimelineMarkers

    public init(markers: TimelineMarkers) {
        self.markers = markers
    }

    public func isPrerollSkippable(at position: TimeInterval, isAdFreeTier: Bool) -> Bool {
        guard let marker = markers.marker(at: position) else { return false }
        return marker.isSkippable || isAdFreeTier
    }

    public func skipTarget(from position: TimeInterval) -> TimeInterval? {
        markers.marker(at: position)?.endTime
    }
}
