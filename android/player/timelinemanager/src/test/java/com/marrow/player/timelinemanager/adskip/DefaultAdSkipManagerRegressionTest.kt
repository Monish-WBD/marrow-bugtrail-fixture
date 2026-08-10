package com.marrow.player.timelinemanager.adskip

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression test for SYN-002: "Skip Intro marker not propagated to overlay for
 * preroll".
 *
 * Attributed by BugTrail to PR #11 "Fix preroll marker propagation to overlays",
 * which added `.filterNot { it.isPreroll }` to `markersForOverlay`.
 *
 * The assertion encodes the behaviour described in the bug report - a skippable
 * preroll must still reach the overlay - rather than the behaviour currently
 * implemented, so it fails at HEAD and passes before the suspect PR.
 *
 * Not executed in CI here: this fixture repository has no Gradle setup, so the
 * Kotlin case is verified by inspection while the Swift case is verified
 * mechanically by tools/bugtrail/verify/verify.sh.
 */
class DefaultAdSkipManagerRegressionTest {

    private val preroll = TimelineMarker(
        startMs = 0, endMs = 5_000, isSkippable = true, isPreroll = true
    )
    private val midroll = TimelineMarker(
        startMs = 10_000, endMs = 20_000, isSkippable = true, isPreroll = false
    )

    private fun manager(vararg markers: TimelineMarker) =
        DefaultAdSkipManager(TimelineMarkerProcessor(markers.toList()))

    @Test
    fun `skippable preroll is still offered to the overlay`() {
        val markers = manager(preroll).markersForOverlay()

        assertTrue(
            "A skippable preroll must reach the overlay, otherwise no skip affordance renders",
            markers.contains(preroll)
        )
    }

    @Test
    fun `non preroll markers are unaffected`() {
        assertEquals(listOf(midroll), manager(midroll).markersForOverlay())
    }

    @Test
    fun `unskippable markers are still excluded`() {
        val unskippable = TimelineMarker(
            startMs = 0, endMs = 1_000, isSkippable = false, isPreroll = false
        )

        assertTrue(manager(unskippable).markersForOverlay().isEmpty())
    }
}
