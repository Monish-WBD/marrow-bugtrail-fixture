import XCTest

/// Regression test for SYN-001: "Skip Intro button missing during preroll on
/// ad-free tier".
///
/// Attributed by BugTrail to PR #5 "Refactor preroll seek mediation", which
/// added an early `return false` for preroll markers in `isPrerollSkippable`.
///
/// The assertion below encodes the behaviour described in the bug report - a
/// skippable preroll must stay skippable - rather than the behaviour currently
/// implemented. It therefore fails at HEAD and passes at the commit before the
/// suspect PR. Run `tools/bugtrail/verify/verify.sh` to confirm both.
final class AdSkipManagerRegressionTests: XCTestCase {

    private func manager(with markers: [TimelineMarker]) -> AdSkipManager {
        AdSkipManager(markers: TimelineMarkers(markers: markers))
    }

    func test_isPrerollSkippable_skippablePrerollRemainsSkippableOnAdFreeTier() {
        let preroll = TimelineMarker(
            startTime: 0, endTime: 5, isSkippable: true, isPreroll: true
        )

        XCTAssertTrue(
            manager(with: [preroll]).isPrerollSkippable(at: 1, isAdFreeTier: true),
            "A skippable preroll must remain skippable, otherwise Skip Intro never appears"
        )
    }

    func test_isPrerollSkippable_nonPrerollIsUnaffected() {
        let midroll = TimelineMarker(
            startTime: 10, endTime: 20, isSkippable: true, isPreroll: false
        )

        XCTAssertTrue(
            manager(with: [midroll]).isPrerollSkippable(at: 12, isAdFreeTier: false),
            "Non-preroll skippable sections were never in scope for this regression"
        )
    }

    func test_isPrerollSkippable_returnsFalseOutsideAnyMarker() {
        let preroll = TimelineMarker(
            startTime: 0, endTime: 5, isSkippable: true, isPreroll: true
        )

        XCTAssertFalse(
            manager(with: [preroll]).isPrerollSkippable(at: 99, isAdFreeTier: true),
            "Positions outside every marker are not skippable"
        )
    }
}
