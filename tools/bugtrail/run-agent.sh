#!/bin/bash
# One BugTrail sweep. Written for a scheduler - launchd, cron or CI - which is
# why it does a single pass and exits rather than looping: a crashed loop stays
# dead until someone notices, whereas a scheduler restarts the next tick.
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
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

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
    python3 "$REPO_ROOT/tools/bugtrail/jira_agent.py" "$@"
} >> "$LOG" 2>&1

tail -n 20 "$LOG"
