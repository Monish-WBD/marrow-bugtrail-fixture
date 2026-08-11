#!/usr/bin/env bash
#
# Seeds this repo with a synthetic, ground-truth-labelled git history for
# testing bug-to-suspect-PR attribution.
#
# Everything here is fabricated. No internal data.
#
# The commit/merge message formats mirror what GitHub actually produces:
#   merge commit : "Merge pull request #N from <owner>/<branch>"
#   squash merge : "<title> (#N)"
#
# Undo with:  git reset --hard origin/main && git clean -fd
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Read from the remote rather than written in. It was pinned to a personal
# account that is no longer where this repository lives, so the synthetic merge
# commits claimed to come from a fork nobody can reach - the one detail in the
# fabricated history that has to look real, because resolve_pr parses it.
OWNER="$(git remote get-url origin 2>/dev/null \
    | sed -E 's|.*[:/]([^/]+)/[^/]+(\.git)?$|\1|')"
OWNER="${OWNER:-unknown-owner}"

if git log --oneline | grep -q "preroll seek mediation"; then
  echo "ERROR: repo already seeded."
  echo "Reset first:  git reset --hard origin/main && git clean -fd"
  exit 1
fi

# Committer identity is passed per-command so the repo config is left untouched.
g() { git -c user.name="Fixture Seeder" -c user.email="fixture@example.com" "$@"; }

# commit <iso-date> <"Name <email>"> <message>
commit() {
  local date="$1" author="$2" msg="$3"
  GIT_AUTHOR_DATE="$date" GIT_COMMITTER_DATE="$date" \
    g commit -q --author="$author" -m "$msg"
}

# merge_pr <iso-date> <branch> <pr-number> <title>
merge_pr() {
  local date="$1" branch="$2" pr="$3" title="$4"
  g checkout -q main
  GIT_AUTHOR_DATE="$date" GIT_COMMITTER_DATE="$date" \
    g merge -q --no-ff "$branch" -m "Merge pull request #${pr} from ${OWNER}/${branch}

${title}"
}

# squash_pr <iso-date> <branch> <pr-number> <"Name <email>"> <title>
squash_pr() {
  local date="$1" branch="$2" pr="$3" author="$4" title="$5"
  g checkout -q main
  g merge -q --squash "$branch"
  commit "$date" "$author" "${title} (#${pr})"
}

ALICE="Alice Kumar <alice@example.com>"
BOB="Bob Sharma <bob@example.com>"
CAROL="Carol Nair <carol@example.com>"
DAN="Dan Roy <dan@example.com>"
EVE="Eve Menon <eve@example.com>"
FRANK="Frank Das <frank@example.com>"
BOT="github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>"

IOS_DIR="TASK/TASK/Modules/Player"
GEN_DIR="TASK/TASK/Generated"
AND_DIR="android/player/timelinemanager/src/main/java/com/marrow/player/timelinemanager/adskip"

g checkout -q main

# ---------------------------------------------------------------------------
# 2026-03-01  chore: CODEOWNERS + seeding tool  (direct to main, no PR)
# ---------------------------------------------------------------------------
mkdir -p tools
cat > CODEOWNERS <<'EOF'
# iOS player
TASK/TASK/Modules/Player/   @marrow-ios-player

# Android player
android/player/             @marrow-android-player

# Generated sources - do not attribute bugs here
TASK/TASK/Generated/        @marrow-tooling
EOF
g add -A
commit "2026-03-01T10:00:00" "$FRANK" "chore: add CODEOWNERS and fixture seeding tool"

# ---------------------------------------------------------------------------
# PR #2 (merge commit)  2026-03-10  iOS player module scaffolding
# ---------------------------------------------------------------------------
g checkout -q -b feature/player-module
mkdir -p "$IOS_DIR"

cat > "$IOS_DIR/TimelineMarkers.swift" <<'EOF'
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
        markers.first { position >= $0.startTime && position < $0.endTime }
    }
}
EOF

cat > "$IOS_DIR/AdSkipManager.swift" <<'EOF'
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
EOF

g add -A
commit "2026-03-10T09:15:00" "$ALICE" "feat: add ad skip manager and timeline markers"
merge_pr "2026-03-10T11:00:00" "feature/player-module" 2 "feat: add ad skip manager and timeline markers"

# ---------------------------------------------------------------------------
# PR #3 (merge commit)  2026-03-20  Android mirror
# ---------------------------------------------------------------------------
g checkout -q -b feature/android-adskip
mkdir -p "$AND_DIR"

cat > "$AND_DIR/TimelineMarkerProcessor.kt" <<'EOF'
package com.marrow.player.timelinemanager.adskip

data class TimelineMarker(
    val startMs: Long,
    val endMs: Long,
    val isSkippable: Boolean,
    val isPreroll: Boolean
)

class TimelineMarkerProcessor(private val markers: List<TimelineMarker> = emptyList()) {

    fun markerAt(positionMs: Long): TimelineMarker? =
        markers.firstOrNull { positionMs >= it.startMs && positionMs < it.endMs }

    fun skippableMarkers(): List<TimelineMarker> = markers.filter { it.isSkippable }
}
EOF

cat > "$AND_DIR/DefaultAdSkipManager.kt" <<'EOF'
package com.marrow.player.timelinemanager.adskip

class DefaultAdSkipManager(private val processor: TimelineMarkerProcessor) {

    fun isPrerollSkippable(positionMs: Long, isAdFreeTier: Boolean): Boolean {
        val marker = processor.markerAt(positionMs) ?: return false
        return marker.isSkippable || isAdFreeTier
    }

    fun markersForOverlay(): List<TimelineMarker> = processor.skippableMarkers()

    fun skipTarget(positionMs: Long): Long? = processor.markerAt(positionMs)?.endMs
}
EOF

g add -A
commit "2026-03-20T09:30:00" "$BOB" "feat(android): add default ad skip manager"
merge_pr "2026-03-20T12:00:00" "feature/android-adskip" 3 "feat(android): add default ad skip manager"

# ---------------------------------------------------------------------------
# PR #4 (merge)  2026-06-20  *** TRUE CULPRIT for SYN-003 (iOS) ***
# Substantive change to TimelineMarkers.swift BEFORE it is renamed in PR #10,
# so attributing this bug is only possible via `git log --follow`.
# ---------------------------------------------------------------------------
g checkout -q -b fix/marker-lookup-boundary
cat > "$IOS_DIR/TimelineMarkers.swift" <<'EOF'
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
EOF
g add -A
commit "2026-06-20T11:30:00" "$ALICE" "fix: make marker lookup boundary inclusive"
merge_pr "2026-06-20T12:00:00" "fix/marker-lookup-boundary" 4 "fix: make marker lookup boundary inclusive"

# ---------------------------------------------------------------------------
# PR #5 (SQUASH)  2026-06-24  *** TRUE CULPRIT for SYN-001 (iOS) ***
# Preroll markers now return false, so Skip Intro never appears on preroll.
# ---------------------------------------------------------------------------
g checkout -q -b fix/preroll-seek-mediation
cat > "$IOS_DIR/AdSkipManager.swift" <<'EOF'
import Foundation

/// Decides whether the current playback position sits inside a skippable section.
public final class AdSkipManager {
    private let markers: TimelineMarkers

    public init(markers: TimelineMarkers) {
        self.markers = markers
    }

    public func isPrerollSkippable(at position: TimeInterval, isAdFreeTier: Bool) -> Bool {
        guard let marker = markers.marker(at: position) else { return false }
        // Preroll is mediated by the seek pipeline now; the skip flag is resolved downstream.
        if marker.isPreroll {
            return false
        }
        return marker.isSkippable || isAdFreeTier
    }

    public func skipTarget(from position: TimeInterval) -> TimeInterval? {
        markers.marker(at: position)?.endTime
    }
}
EOF
g add -A
commit "2026-06-24T14:05:00" "$CAROL" "refactor preroll seek mediation"
squash_pr "2026-06-24T15:00:00" "fix/preroll-seek-mediation" 5 "$CAROL" "Refactor preroll seek mediation"

# ---------------------------------------------------------------------------
# PR #6 (merge)  2026-06-25  decoy: unrelated file, same timeframe
# ---------------------------------------------------------------------------
g checkout -q -b chore/profile-styling
printf '\n// MARK: - Styling pass (spacing tweaks)\n' >> "TASK/TASK/Modules/View/ProfileView.swift"
g add -A
commit "2026-06-25T10:00:00" "$DAN" "chore: profile view styling pass"
merge_pr "2026-06-25T10:30:00" "chore/profile-styling" 6 "chore: profile view styling pass"

# ---------------------------------------------------------------------------
# PR #7 (SQUASH)  2026-06-26  decoy: touches the culprit FILE but only comments
# ---------------------------------------------------------------------------
g checkout -q -b chore/adskip-comments
printf '\n// Note: skip eligibility is evaluated per playback position.\n' >> "$IOS_DIR/AdSkipManager.swift"
g add -A
commit "2026-06-26T09:00:00" "$EVE" "docs: clarify ad skip eligibility"
squash_pr "2026-06-26T09:20:00" "chore/adskip-comments" 7 "$EVE" "Clarify ad skip eligibility comments"

# ---------------------------------------------------------------------------
# 2026-06-27  direct commit to main, no PR  (tests the no-PR fallback)
# ---------------------------------------------------------------------------
g checkout -q main
printf '\n// hotfix: widen session timer tolerance\n' >> "TASK/TASK/Modules/ViewModel/FocusSessionManager.swift"
g add -A
commit "2026-06-27T22:40:00" "$FRANK" "hotfix: widen session timer tolerance"

# ---------------------------------------------------------------------------
# PR #8 (merge)  2026-06-28  revert of PR #6  (tests revert exclusion)
# ---------------------------------------------------------------------------
g checkout -q -b revert/profile-styling
printf '\n// Reverted styling pass.\n' >> "TASK/TASK/Modules/View/ProfileView.swift"
g add -A
commit "2026-06-28T11:00:00" "$DAN" "Revert \"chore: profile view styling pass\""
merge_pr "2026-06-28T11:15:00" "revert/profile-styling" 8 "Revert \"chore: profile view styling pass\""

# ---------------------------------------------------------------------------
# 2026-06-29  bot commit on generated code  (tests bot + generated exclusion)
# ---------------------------------------------------------------------------
g checkout -q main
mkdir -p "$GEN_DIR"
cat > "$GEN_DIR/AnalyticsEvents.swift" <<'EOF'
// GENERATED CODE DO NOT MODIFY
//
// Generated from analytics-schema v1.4.0

public enum AnalyticsEvent: String {
    case sessionStarted = "session_started"
    case sessionCompleted = "session_completed"
    case skipIntroTapped = "skip_intro_tapped"
}
EOF
g add -A
commit "2026-06-29T03:12:00" "$BOT" "chore(deps): regenerate analytics events from schema v1.4.0"

# ---------------------------------------------------------------------------
# PR #10 (merge)  2026-06-30  file rename  (tests git log --follow)
# ---------------------------------------------------------------------------
g checkout -q -b refactor/timeline-marker-store
g mv "$IOS_DIR/TimelineMarkers.swift" "$IOS_DIR/TimelineMarkerStore.swift"
printf '\n// Renamed from TimelineMarkers for clarity.\n' >> "$IOS_DIR/TimelineMarkerStore.swift"
g add -A
commit "2026-06-30T16:20:00" "$ALICE" "refactor: rename TimelineMarkers to TimelineMarkerStore"
merge_pr "2026-06-30T16:45:00" "refactor/timeline-marker-store" 10 "refactor: rename TimelineMarkers to TimelineMarkerStore"

# ---------------------------------------------------------------------------
# PR #11 (SQUASH)  2026-07-01  *** TRUE CULPRIT for SYN-002 (Android) ***
# Preroll markers are filtered out before reaching the overlay.
# ---------------------------------------------------------------------------
g checkout -q -b fix/preroll-marker-propagation
cat > "$AND_DIR/DefaultAdSkipManager.kt" <<'EOF'
package com.marrow.player.timelinemanager.adskip

class DefaultAdSkipManager(private val processor: TimelineMarkerProcessor) {

    fun isPrerollSkippable(positionMs: Long, isAdFreeTier: Boolean): Boolean {
        val marker = processor.markerAt(positionMs) ?: return false
        return marker.isSkippable || isAdFreeTier
    }

    // Preroll markers are propagated by the ad break pipeline instead of the overlay.
    fun markersForOverlay(): List<TimelineMarker> =
        processor.skippableMarkers().filterNot { it.isPreroll }

    fun skipTarget(positionMs: Long): Long? = processor.markerAt(positionMs)?.endMs
}
EOF
g add -A
commit "2026-07-01T13:10:00" "$BOB" "fix preroll marker propagation to overlays"
squash_pr "2026-07-01T13:40:00" "fix/preroll-marker-propagation" 11 "$BOB" "Fix preroll marker propagation to overlays"

# ---------------------------------------------------------------------------
# Ground truth manifest
# ---------------------------------------------------------------------------
g checkout -q main
mkdir -p fixtures
cat > fixtures/ground-truth.json <<'EOF'
{
  "description": "Synthetic bugs with known culprit PRs, for scoring suspect-PR attribution.",
  "reportedAt": "2026-07-02T04:00:00Z",
  "bugs": [
    {
      "bugId": "SYN-001",
      "platform": "ios",
      "title": "Skip Intro button missing during preroll on ad-free tier",
      "seedFile": "TASK/TASK/Modules/Player/AdSkipManager.swift",
      "truePR": 5,
      "trueAuthor": "Carol Nair",
      "mergeStrategy": "squash",
      "tests": ["keyword match on preroll/skip", "decoy PR #7 touches same file", "decoy PR #6 same timeframe"]
    },
    {
      "bugId": "SYN-002",
      "platform": "android",
      "title": "Skip Intro marker not propagated to overlay for preroll",
      "seedFile": "android/player/timelinemanager/src/main/java/com/marrow/player/timelinemanager/adskip/DefaultAdSkipManager.kt",
      "truePR": 11,
      "trueAuthor": "Bob Sharma",
      "mergeStrategy": "squash",
      "tests": ["platform routing from .kt path", "module expansion"]
    },
    {
      "bugId": "SYN-003",
      "platform": "ios",
      "title": "Timeline marker lookup returns a stale marker at boundary",
      "seedFile": "TASK/TASK/Modules/Player/TimelineMarkerStore.swift",
      "truePR": 4,
      "trueAuthor": "Alice Kumar",
      "mergeStrategy": "merge",
      "tests": [
        "git log --follow across the PR #10 rename",
        "culprit predates the rename, so it is unreachable without --follow",
        "the rename itself (PR #10) must not outrank the real change"
      ]
    }
  ],
  "exclusionCases": {
    "botCommit": "chore(deps): regenerate analytics events from schema v1.4.0",
    "generatedFile": "TASK/TASK/Generated/AnalyticsEvents.swift",
    "revertPR": 8,
    "directCommitNoPR": "hotfix: widen session timer tolerance"
  }
}
EOF

cat > .gitignore <<'EOF'
.DS_Store
xcuserdata/

# Local-only test fixtures. Never commit CodeSage-format samples to a public repo.
fixtures/codesage/
EOF

g add -A
commit "2026-07-02T08:00:00" "$FRANK" "test: add ground truth manifest for attribution fixtures"

echo
echo "Seeding complete."
echo
g log --oneline -20
