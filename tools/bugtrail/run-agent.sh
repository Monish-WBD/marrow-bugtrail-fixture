#!/bin/bash
# Runs BugTrail under a supervisor - launchd, cron or CI. It passes its
# arguments straight through, so the caller picks the shape:
#
#     --once                    one sweep, for a scheduler that ticks
#     --watch --interval 15     stay up and poll, for a supervisor that restarts
#
# A looping process is only safe when something is watching it, because a
# crashed loop stays dead until a human notices. Under launchd with KeepAlive,
# something is.
#
# Credentials live outside the repository, in a file only you can read:
#
#     mkdir -p ~/.config/bugtrail
#     cat > ~/.config/bugtrail/env <<'EOF'
#     export JIRA_BASE_URL=https://wbdstreaming.atlassian.net
#     export JIRA_EMAIL=you@wbd.com
#     export JIRA_API_TOKEN=paste-token-here
#     EOF
#     chmod 600 ~/.config/bugtrail/env
#
# A token in a file the whole machine can read is a token you have to rotate.

set -euo pipefail

ENV_FILE="${BUGTRAIL_ENV:-$HOME/.config/bugtrail/env}"
LOG_DIR="${BUGTRAIL_LOG_DIR:-$HOME/.cache/bugtrail/logs}"

# Resolved relative to this script rather than to a repository root, so the same
# script works from a checkout and from the copy the installer places outside
# ~/Documents. See deploy/install-agent.sh for why that copy has to exist.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
else
    echo "no credentials at $ENV_FILE - see the header of this script" >&2
    exit 2
fi

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/agent.log"

# Everything is appended with a timestamp: when the bot says something odd on a
# ticket, the first question is always which run produced it.
{
    echo "===== $(date '+%Y-%m-%d %H:%M:%S %z')"
    # -u because in watch mode this process never exits, and a buffered log is
    # an empty log: you cannot tell a working watcher from a wedged one.
    python3 -u "$HERE/jira_agent.py" "$@"
} >> "$LOG" 2>&1
