#!/bin/bash
# Install BugTrail as a launchd service, so it answers tickets whether or not a
# terminal is open.
#
# Re-running this is the way to apply a changed plist: it tears the old job down
# first, because launchd will not notice an edited file on its own and you end
# up debugging behaviour that is no longer in the file you are reading.
#
# Usage:
#     deploy/install-agent.sh                    # defaults to PLAY-126471
#     deploy/install-agent.sh --parent PLAY-999  # any other scope
#
# Uninstall:
#     launchctl bootout gui/$(id -u)/com.bugtrail.agent

set -euo pipefail

LABEL="com.bugtrail.agent"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/deploy/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
INSTALL_DIR="${BUGTRAIL_INSTALL_DIR:-$HOME/.local/share/bugtrail}"
ENV_FILE="${BUGTRAIL_ENV:-$HOME/.config/bugtrail/env}"
LOG_DIR="$HOME/.cache/bugtrail/logs"

if [ ! -f "$ENV_FILE" ]; then
    echo "no credentials at $ENV_FILE" >&2
    echo "see the header of tools/bugtrail/run-agent.sh" >&2
    exit 2
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR" "$INSTALL_DIR"

# The service runs a copy, not the checkout. macOS gates ~/Documents, ~/Desktop
# and ~/Downloads behind per-application consent, and a launchd job inherits
# none of what your terminal was granted - it just gets "Operation not
# permitted" and retries forever. Copying to ~/.local/share sidesteps that
# without granting Full Disk Access to /bin/bash, which is a large permission
# to hand out for one script.
cp "$REPO_ROOT/tools/bugtrail/"*.py "$REPO_ROOT/tools/bugtrail/"*.sh \
   "$REPO_ROOT/tools/bugtrail/config.json" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/run-agent.sh"

sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" -e "s|__HOME__|$HOME|g" \
    "$TEMPLATE" > "$TARGET"

# A malformed plist fails at bootstrap with a number, not a reason.
plutil -lint "$TARGET" > /dev/null

# Any argument given here overrides the scope baked into the template, so the
# same service can be pointed at a different story without editing XML.
if [ "$#" -gt 0 ]; then
    python3 - "$TARGET" "$@" <<'PY'
import plistlib, sys
target, args = sys.argv[1], sys.argv[2:]
with open(target, "rb") as f:
    plist = plistlib.load(f)
script = plist["ProgramArguments"][1]
plist["ProgramArguments"] = ["/bin/bash", script] + args
with open(target, "wb") as f:
    plistlib.dump(plist, f)
PY
fi

DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$TARGET"
launchctl enable "$DOMAIN/$LABEL"

sleep 3
# "Loaded" is not "working": a job that cannot execute its program is still
# loaded, and launchd keeps rescheduling it. The exit status is the honest
# signal, so report that rather than the job's presence.
LAST_EXIT="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null \
    | awk -F'= ' '/last exit code/ {print $2; exit}')"

if [ -z "$LAST_EXIT" ]; then
    echo "job did not load; check $LOG_DIR/launchd-err.log" >&2
    exit 1
fi

if [ "$LAST_EXIT" != "(never exited)" ] && [ "$LAST_EXIT" != "0" ]; then
    echo "job loaded but exited with $LAST_EXIT" >&2
    tail -n 5 "$LOG_DIR/launchd-err.log" >&2 2>/dev/null || true
    exit 1
fi

echo "installed and running: $LABEL"
echo "  running: $INSTALL_DIR (a copy; re-run this script after code changes)"
echo "  log:     $LOG_DIR/agent.log"
echo "  stop:    launchctl bootout $DOMAIN/$LABEL"
