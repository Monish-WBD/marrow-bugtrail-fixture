import Foundation

public struct TimelineMarker {
    public let startTime: TimeInterval
    public let endTime: TimeInterval
    public let isSkippable: Bool
    public let isPreroll: Bool

    public init(startTime: TimeInterval, endTime: TimeInterval, isSkippable: Bool, isPreroll: Bool) {
        self.startTime = startTime
        self.endTime = endTime
        self.isSkippable = isSkippable
        self.isPreroll = isPreroll
    }
}

public final class TimelineMarkers {
    private let markers: [TimelineMarker]

    public init(markers: [TimelineMarker] = []) {
        self.markers = markers
    }

    public func marker(at position: TimeInterval) -> TimelineMarker? {
        markers.last { position >= $0.startTime && position <= $0.endTime }
    }
}
