#!/usr/bin/env bash
#
# The whole story in one command: a bug report becomes a named pull request
# with a proven failing test.
#
# Usage:
#   ./demo.sh            # Android case (SYN-002)
#   ./demo.sh SYN-001    # iOS case
#   ./demo.sh SYN-003    # renamed-file case
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

BUG="${1:-SYN-002}"
COMMENT="fixtures/triage/${BUG}.txt"

[ -f "$COMMENT" ] || { echo "No such bug: $BUG (try SYN-001, SYN-002, SYN-003)"; exit 1; }

banner() {
  echo
  echo "################################################################################"
  echo "## $1"
  echo "################################################################################"
  echo
}

banner "STEP 1  Triage comment in, suspect pull request out"
# --repo . keeps the demo hermetic: config points the agent at the GitHub repo,
# and this must not start cloning midway through a presentation.
python3 tools/bugtrail/cli.py --repo . --comment "$COMMENT" --report

# Only the iOS AdSkipManager case has a compiled assertion to check against.
if [ "$BUG" = "SYN-001" ]; then
  banner "STEP 2  Proving the drafted test isolates that pull request"
  tools/bugtrail/verify/verify.sh
else
  banner "STEP 2  Proving a drafted test isolates its pull request (SYN-001)"
  echo "Verification compiles Swift, so it runs against the iOS case."
  echo
  tools/bugtrail/verify/verify.sh
fi

echo
echo "################################################################################"
echo "## Nothing here needed a network, an API key, or an issue tracker."
echo "################################################################################"
