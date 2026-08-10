import Foundation

// Mirrors AdSkipManagerRegressionTests.test_isPrerollSkippable_skippablePreroll
// RemainsSkippableOnAdFreeTier, without needing an XCTest target.
//
// Exits 0 when the expected behaviour holds, 1 when it does not, so the same
// assertion can be run against two revisions and the results compared.

let preroll = TimelineMarker(
    startTime: 0, endTime: 5, isSkippable: true, isPreroll: true
)
let manager = AdSkipManager(markers: TimelineMarkers(markers: [preroll]))

let skippable = manager.isPrerollSkippable(at: 1, isAdFreeTier: true)

if skippable {
    print("PASS  a skippable preroll remains skippable on the ad-free tier")
    exit(0)
} else {
    print("FAIL  a skippable preroll was reported as not skippable")
    print("      Skip Intro would never appear during preroll")
    exit(1)
}
