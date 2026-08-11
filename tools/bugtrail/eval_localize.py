"""Measure the localizer against ground truth taken from the repository itself.

Ground truth needs no labelling effort and no opinion: commit subjects carry
their ticket id, so the files a ticket's fix commit touched are, by definition,
the files that ticket was about.

Two rules keep the measurement honest:
  - Search at the parent of the earliest fix commit, never at HEAD, so the fix
    cannot leak into the evidence used to predict it.
  - Feed the localizer only the ticket's title and description. No comments, and
    in particular no upstream triage comment naming the answer.

Usage:
    python3 tools/bugtrail/eval_localize.py --repo ../wbd-beam-swift \
        --tickets .local/tickets.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))

from localize import localize  # noqa: E402

SOURCE_SUFFIXES = (".swift", ".kt", ".java", ".m", ".mm", ".h", ".tsx", ".ts")
TICKET_RE = re.compile(r"\b(PLAY-\d+)\b")


def git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo] + list(args), capture_output=True, text=True
    ).stdout


def build_ground_truth(repo: str, scan: int, max_files: int) -> Dict[str, dict]:
    """ticket -> {files, base_rev} derived from commits that name the ticket."""
    log = git(repo, "log", "--pretty=%H%x00%s", "-%d" % scan)

    commits: Dict[str, List[str]] = defaultdict(list)
    for line in log.strip().split("\n"):
        if "\x00" not in line:
            continue
        sha, subject = line.split("\x00", 1)
        m = TICKET_RE.search(subject)
        if m:
            commits[m.group(1)].append(sha)

    truth: Dict[str, dict] = {}
    for ticket, shas in commits.items():
        files: Set[str] = set()
        for sha in shas:
            out = git(repo, "show", "--name-only", "--pretty=format:", sha)
            files |= {
                f.strip() for f in out.split("\n")
                if f.strip().endswith(SOURCE_SUFFIXES) and "/Tests/" not in f
            }
        if not files or len(files) > max_files:
            continue
        # git log lists newest first, so the last sha is the earliest fix.
        truth[ticket] = {"files": sorted(files), "base_rev": shas[-1] + "^"}
    return truth


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tickets", required=True, help="JSON: key -> {summary, description}")
    ap.add_argument("--scan", type=int, default=6000)
    ap.add_argument("--max-files", type=int, default=15)
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    truth = build_ground_truth(args.repo, args.scan, args.max_files)
    tickets = json.loads(Path(args.tickets).read_text())

    evaluated = [k for k in tickets if k in truth]
    print("ground truth tickets : %d (from last %d commits)" % (len(truth), args.scan))
    print("ticket text available: %d" % len(tickets))
    print("evaluating           : %d\n" % len(evaluated))

    hits = {1: 0, 3: 0, 5: 0}
    dir_hits = {1: 0, 5: 0}
    lib_hits = {5: 0}
    no_candidates = 0
    misses = []

    def directory(p: str) -> str:
        return p.rsplit("/", 1)[0] if "/" in p else ""

    def library(p: str) -> str:
        # libraries/<Name>/... or apps/<Name>/... - the unit a team owns.
        parts = p.split("/")
        return "/".join(parts[:2]) if len(parts) > 2 else parts[0]

    for key in sorted(evaluated):
        t = tickets[key]
        text = "%s\n\n%s" % (t.get("summary", ""), t.get("description", ""))
        gold = set(truth[key]["files"])

        ranked = localize(
            text, args.repo, limit=args.limit, rev=truth[key]["base_rev"]
        )
        paths = [c.path for c in ranked]
        if not paths:
            no_candidates += 1
            misses.append((key, t.get("summary", "")[:60], "no candidates"))
            continue

        for k in (1, 3, 5):
            if gold & set(paths[:k]):
                hits[k] += 1

        gold_dirs = {directory(g) for g in gold}
        gold_libs = {library(g) for g in gold}
        for k in (1, 5):
            if {directory(p) for p in paths[:k]} & gold_dirs:
                dir_hits[k] += 1
        if {library(p) for p in paths[:5]} & gold_libs:
            lib_hits[5] += 1

        if args.verbose or not (gold & set(paths[: args.limit])):
            mark = "HIT " if gold & set(paths[: args.limit]) else "MISS"
            print("%s %-13s %s" % (mark, key, t.get("summary", "")[:62]))
            if mark == "MISS":
                print("      predicted: %s" % (paths[0] if paths else "-"))
                print("      actual   : %s" % sorted(gold)[0])

    n = len(evaluated) or 1
    pct = lambda v: 100.0 * v / n

    print("\n=== results over %d tickets ===" % len(evaluated))
    print("exact file")
    for k in (1, 3, 5):
        print("  top-%d          %3d/%d  %5.1f%%" % (k, hits[k], len(evaluated), pct(hits[k])))
    print("same directory   (module expansion reaches the fix from here)")
    for k in (1, 5):
        print("  top-%d          %3d/%d  %5.1f%%" % (k, dir_hits[k], len(evaluated), pct(dir_hits[k])))
    print("same library     (correct team and component)")
    print("  top-5          %3d/%d  %5.1f%%" % (lib_hits[5], len(evaluated), pct(lib_hits[5])))
    print("no candidates produced: %d" % no_candidates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
