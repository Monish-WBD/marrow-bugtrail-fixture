#!/usr/bin/env bash
#
# Confirms a drafted regression test fails for the right reason.
#
# A regression test that passes proves nothing. This compiles the same assertion
# against two revisions:
#
#   HEAD                     -> expected to FAIL (the bug is present)
#   parent of the suspect PR -> expected to PASS (the bug is absent)
#
# Both outcomes together are what actually confirms the attribution: the
# behaviour changed at that commit and nowhere else.
#
# Requires swiftc. No Xcode project or test target needed.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

VERIFY_DIR="tools/bugtrail/verify"
BUILD="$REPO_ROOT/.build/verify"
SOURCE="TASK/TASK/Modules/Player/AdSkipManager.swift"
SUPPORT="TASK/TASK/Modules/Player/TimelineMarkerStore.swift"
SUSPECT_SUBJECT="Refactor preroll seek mediation"

command -v swiftc >/dev/null || { echo "swiftc not found; install Xcode command line tools"; exit 2; }

SUSPECT_SHA="$(git log --format=%H --grep="$SUSPECT_SUBJECT" -1 || true)"
if [ -z "$SUSPECT_SHA" ]; then
  echo "Could not find the suspect commit. Has the fixture been seeded?"
  exit 2
fi

rm -rf "$BUILD" && mkdir -p "$BUILD"

# One assertion, two revisions of the file under test.
git show "HEAD:$SOURCE"            > "$BUILD/AdSkipManager.head.swift"
git show "$SUSPECT_SHA^:$SOURCE"   > "$BUILD/AdSkipManager.before.swift"
git show "HEAD:$SUPPORT"           > "$BUILD/TimelineMarkerStore.swift"
cp "$VERIFY_DIR/main.swift" "$BUILD/main.swift"

run_at() { # run_at <label> <variant-file> <expected: fail|pass>
  local label="$1" variant="$2" expected="$3" binary="$BUILD/check_$1"
  swiftc -O -o "$binary" "$variant" "$BUILD/TimelineMarkerStore.swift" "$BUILD/main.swift" 2>"$BUILD/$1.build.log" || {
    echo "  build error:"; sed 's/^/    /' "$BUILD/$1.build.log"; return 2
  }
  local output status
  set +e
  output="$("$binary")"
  status=$?
  set -e
  echo "  $output"
  if [ "$expected" = "fail" ] && [ "$status" -ne 0 ]; then
    echo "  => as expected: the test catches the bug"
    return 0
  fi
  if [ "$expected" = "pass" ] && [ "$status" -eq 0 ]; then
    echo "  => as expected: the behaviour was intact here"
    return 0
  fi
  echo "  => UNEXPECTED (exit $status)"
  return 1
}

echo "Regression check: SYN-001 / AdSkipManager.isPrerollSkippable"
echo "Suspect: ${SUSPECT_SHA:0:7}  $SUSPECT_SUBJECT"
echo

ok=0
echo "at HEAD (bug present, expect FAIL)"
run_at head "$BUILD/AdSkipManager.head.swift" fail || ok=1
echo
echo "at ${SUSPECT_SHA:0:7}^ (before the suspect PR, expect PASS)"
run_at before "$BUILD/AdSkipManager.before.swift" pass || ok=1
echo

if [ "$ok" -eq 0 ]; then
  echo "CONFIRMED: behaviour changed at the suspect commit. Attribution holds."
else
  echo "NOT CONFIRMED: the assertion does not isolate this commit."
  echo "If it fails at both revisions, the assertion is wrong rather than the code."
fi
exit "$ok"
