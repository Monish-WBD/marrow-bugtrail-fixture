import Foundation

/// A single quality sample captured by the player.
public struct PlaybackQualitySample: Equatable {
    public let timestamp: Date
    public let bitrateKbps: Int
    public let bufferedDuration: TimeInterval
    public let droppedFrames: Int

    public init(timestamp: Date, bitrateKbps: Int, bufferedDuration: TimeInterval, droppedFrames: Int) {
        self.timestamp = timestamp
        self.bitrateKbps = bitrateKbps
        self.bufferedDuration = bufferedDuration
        self.droppedFrames = droppedFrames
    }
}

/// Verdicts the monitor can hand back to the player after each sample.
public enum QualityVerdict: Equatable {
    case healthy
    case degradedBuffer
    case degradedFrames
    case rebufferImminent
}

/// Rolling quality monitor that emits verdicts based on a sliding window
/// of recent samples. It intentionally does no I/O — the caller decides how
/// to react to the verdict (downshift ABR, log an event, etc).
public final class PlaybackQualityMonitor {
    /// Configurable thresholds. Tuned against the 2026.08 rollout metrics.
    public struct Thresholds: Equatable {
        public let minHealthyBufferedDuration: TimeInterval
        public let rebufferBufferedDuration: TimeInterval
        public let droppedFramesPerWindow: Int
        public let windowSize: Int

        public init(
            minHealthyBufferedDuration: TimeInterval = 4.0,
            rebufferBufferedDuration: TimeInterval = 1.0,
            droppedFramesPerWindow: Int = 12,
            windowSize: Int = 5
        ) {
            self.minHealthyBufferedDuration = minHealthyBufferedDuration
            self.rebufferBufferedDuration = rebufferBufferedDuration
            self.droppedFramesPerWindow = droppedFramesPerWindow
            self.windowSize = windowSize
        }

        public static let `default` = Thresholds()
    }

    private let thresholds: Thresholds
    private var window: [PlaybackQualitySample] = []

    public init(thresholds: Thresholds = .default) {
        self.thresholds = thresholds
    }

    /// Feeds a new sample into the sliding window and returns the current verdict.
    @discardableResult
    public func record(_ sample: PlaybackQualitySample) -> QualityVerdict {
        window.append(sample)
        if window.count > thresholds.windowSize {
            window.removeFirst(window.count - thresholds.windowSize)
        }
        return currentVerdict()
    }

    /// Returns the verdict for the current window without mutating state.
    public func currentVerdict() -> QualityVerdict {
        guard let latest = window.last else { return .healthy }

        if latest.bufferedDuration <= thresholds.rebufferBufferedDuration {
            return .rebufferImminent
        }

        let droppedInWindow = window.reduce(0) { $0 + $1.droppedFrames }
        if droppedInWindow >= thresholds.droppedFramesPerWindow {
            return .degradedFrames
        }

        if latest.bufferedDuration < thresholds.minHealthyBufferedDuration {
            return .degradedBuffer
        }

        return .healthy
    }

    /// Clears the window, e.g. when the player is torn down or switched to a new asset.
    public func reset() {
        window.removeAll(keepingCapacity: true)
    }
}
