#!/usr/bin/env python3
"""Derive bug ground truth from git history alone, SZZ-style.

Jira records which PR *fixed* a bug, never which one caused it, so ground truth
for attribution does not exist as a field anywhere. The standard workaround
(Sliwerski-Zimmermann-Zeller) recovers it from the repository:

    take a commit that fixed a bug
      -> find the lines it changed
      -> blame those lines at the fix's parent
      -> the commit that last wrote them is the likely cause

That needs no Jira, no API, and no credentials - only commit messages carrying
ticket ids, which this repository's history already has.

Honest limits, which belong on any slide reporting these numbers:
  - This is *derived* ground truth, not human-verified. Blame can land on a
    reformat, or on the commit that moved code rather than the one that broke it.
  - Only single-file fixes are used, since multi-file fixes make the causing
    change ambiguous. That biases toward simpler bugs.
  - Pure additions are skipped: there are no prior lines to blame.

Usage:
    python3 tools/bugtrail/mine_szz.py --repo <path> --out <manifest.json>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archaeology import (  # noqa: E402
    GitError,
    _parse_commit,
    is_bot,
    is_revert,
    resolve_pr,
    run_git,
)

_FIELD_SEP = "\x1f"
_TICKET = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_FIX_SUBJECT = re.compile(r"^(?:fix|bugfix|hotfix)\b", re.IGNORECASE)
_OLD_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+")
_BLAME_SHA = re.compile(r"^([0-9a-f]{40}) \d+ \d+")

BOT_PATTERNS = [r"\[bot\]", r"^dependabot", r"^github-actions", r"^svc-"]

CODE_SUFFIXES = (".swift", ".kt", ".java")


def fix_commits(repo: str, scan: int, require_ticket: bool = False) -> list:
    fmt = _FIELD_SEP.join(["%H", "%an", "%ae", "%ad", "%s", "%P"])
    out = run_git(
        repo,
        "log",
        "--no-merges",
        "--date=iso-strict",
        "--format=%s" % fmt,
        "-%d" % scan,
    )
    commits = []
    for line in out.splitlines():
        commit = _parse_commit(line)
        if commit is None:
            continue
        if not _FIX_SUBJECT.match(commit.subject):
            continue
        if require_ticket and not _TICKET.search(commit.subject):
            continue
        if is_bot(commit, BOT_PATTERNS) or is_revert(commit):
            continue
        commits.append(commit)
    return commits


def single_code_file(repo: str, sha: str):
    """The one code file a commit changed, or None if it touched several."""
    try:
        out = run_git(repo, "show", "--format=", "--name-only", sha)
    except GitError:
        return None
    files = [f for f in out.splitlines() if f.strip().endswith(CODE_SUFFIXES)]
    if len(files) != 1:
        return None
    return files[0]


def touched_old_ranges(repo: str, sha: str, path: str) -> list:
    """Line ranges on the pre-fix side of the diff, which are what to blame."""
    try:
        out = run_git(repo, "diff", "-U0", "%s^" % sha, sha, "--", path)
    except GitError:
        return []
    ranges = []
    for line in out.splitlines():
        m = _OLD_HUNK.match(line)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        if count == 0:
            continue
        ranges.append((start, count))
    return ranges


def blame_causes(repo: str, sha: str, path: str, ranges: list) -> Counter:
    counts = Counter()
    for start, count in ranges:
        try:
            out = run_git(
                repo,
                "blame",
                "--porcelain",
                "-L",
                "%d,+%d" % (start, count),
                "%s^" % sha,
                "--",
                path,
            )
        except GitError:
            continue
        for line in out.splitlines():
            m = _BLAME_SHA.match(line)
            if m:
                counts[m.group(1)] += 1
    return counts


def commit_of(repo: str, sha: str):
    fmt = _FIELD_SEP.join(["%H", "%an", "%ae", "%ad", "%s", "%P"])
    try:
        out = run_git(repo, "show", "-s", "--date=iso-strict", "--format=%s" % fmt, sha)
    except GitError:
        return None
    return _parse_commit(out.splitlines()[0]) if out.splitlines() else None


def mine(repo: str, scan: int, max_bugs: int, verbose: bool,
         require_ticket: bool = False) -> list:
    bugs = []
    skipped = Counter()

    for fix in fix_commits(repo, scan, require_ticket):
        if len(bugs) >= max_bugs:
            break

        path = single_code_file(repo, fix.sha)
        if path is None:
            skipped["not a single-file code fix"] += 1
            continue

        ranges = touched_old_ranges(repo, fix.sha, path)
        if not ranges:
            skipped["pure addition, nothing to blame"] += 1
            continue

        counts = blame_causes(repo, fix.sha, path, ranges)
        if not counts:
            skipped["blame returned nothing"] += 1
            continue

        cause = None
        for sha, _ in counts.most_common():
            candidate = commit_of(repo, sha)
            if candidate is None:
                continue
            if candidate.sha == fix.sha or candidate.is_merge:
                continue
            if is_bot(candidate, BOT_PATTERNS) or is_revert(candidate):
                continue
            if candidate.authored_at >= fix.authored_at:
                continue
            pr = resolve_pr(repo, candidate)
            if pr is None:
                continue
            cause = (candidate, pr)
            break

        if cause is None:
            skipped["no attributable cause"] += 1
            continue

        candidate, pr = cause
        found = _TICKET.search(fix.subject)
        ticket = found.group(1) if found else "FIX-%s" % fix.sha[:7]

        # Reporting just before the fix keeps the fix itself out of the candidate
        # set, via the engine's existing "landed after the report" rule.
        reported_at = fix.authored_at - timedelta(seconds=1)

        bugs.append(
            {
                "bugId": ticket,
                "title": fix.subject,
                "seedFile": path,
                "reportedAt": reported_at.isoformat(),
                "truePR": pr,
                "trueAuthor": candidate.author_name,
                "derivedFrom": {
                    "fixCommit": fix.sha[:7],
                    "causeCommit": candidate.sha[:7],
                    "blamedLines": sum(counts.values()),
                },
            }
        )
        if verbose:
            print(
                "  %-16s fix %s -> cause PR #%-6s %s"
                % (ticket, fix.sha[:7], pr, path.split("/")[-1]),
                file=sys.stderr,
            )

    if verbose:
        print("\nskipped:", file=sys.stderr)
        for reason, n in skipped.most_common():
            print("  %-32s %d" % (reason, n), file=sys.stderr)

    return bugs


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive bug ground truth via SZZ")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--scan", type=int, default=4000, help="commits to scan")
    parser.add_argument("--max-bugs", type=int, default=40)
    parser.add_argument("--out", required=True)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--require-ticket",
        action="store_true",
        help="only use fixes whose subject carries a ticket id",
    )
    args = parser.parse_args()

    bugs = mine(
        args.repo,
        args.scan,
        args.max_bugs,
        verbose=not args.quiet,
        require_ticket=args.require_ticket,
    )
    if not bugs:
        print("No bugs derived. Try a larger --scan.", file=sys.stderr)
        return 1

    manifest = {
        "description": (
            "Ground truth derived from git history via SZZ blame. Automatically "
            "derived, not human-verified."
        ),
        "derivedFrom": args.repo,
        "bugs": bugs,
    }
    Path(args.out).write_text(json.dumps(manifest, indent=2) + "\n")
    print("Wrote %d bugs to %s" % (len(bugs), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
