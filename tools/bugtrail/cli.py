#!/usr/bin/env python3
"""BugTrail: turn a triage seed (file + bug text) into ranked suspect PRs.

The engine is deliberately separated from its I/O. A source yields normalised
TriageInput objects; a sink renders Results. Today the source is the local
fixture manifest and the sink is the console, so the pipeline runs fully offline.
Adding Jira later touches neither archaeology nor ranking.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from archaeology import build_candidates
from codesage import parse_comment
from models import Result, TriageInput
from ranking import confidence_label, extract_keywords, rank

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]


def load_config(path: Path) -> dict:
    return json.loads(path.read_text())


def fixture_source(manifest_path: Path) -> list:
    data = json.loads(manifest_path.read_text())
    reported_at = datetime.fromisoformat(data["reportedAt"].replace("Z", "+00:00"))
    return [
        TriageInput(
            bug_id=bug["bugId"],
            title=bug["title"],
            seed_file=bug["seedFile"],
            reported_at=reported_at,
            platform=bug.get("platform"),
        )
        for bug in data["bugs"]
    ]


def codesage_source(
    comment_path: Path, bug_id: str, reported_at: datetime
) -> TriageInput:
    """Build a seed from a CodeSage comment on disk.

    Same shape a Jira source would produce, so the engine cannot tell them apart.
    """
    triage = parse_comment(comment_path.read_text())
    if triage is None:
        raise ValueError(
            "%s is not a parseable CodeSage comment (no marker or no 'File:' line)"
            % comment_path
        )

    # The comment carries no title, so the summary stands in for keyword mining.
    return TriageInput(
        bug_id=bug_id,
        title=triage.summary or triage.where_to_start,
        seed_file=triage.seed_file,
        reported_at=reported_at,
        platform=triage.platform,
    )


def analyse(repo: str, bug: TriageInput, config: dict) -> Result:
    keywords = extract_keywords(bug.title)
    candidates, excluded = build_candidates(
        repo=repo,
        seed_file=bug.seed_file,
        reported_at=bug.reported_at,
        keywords=keywords,
        config=config,
    )
    suspects, confidence = rank(candidates, bug.reported_at, keywords, config)
    notes = ["keywords: " + ", ".join(keywords)] if keywords else []
    return Result(
        bug=bug,
        suspects=suspects,
        confidence=confidence,
        excluded=excluded,
        notes=notes,
    )


def _headline(text: str, width: int = 66) -> str:
    single_line = " ".join(text.split())
    if len(single_line) <= width:
        return single_line
    return single_line[: width - 3].rstrip() + "..."


def render(result: Result) -> str:
    bug = result.bug
    out = []
    out.append("=" * 74)
    out.append("%s  %s" % (bug.bug_id, _headline(bug.title)))
    out.append("seed: %s" % bug.seed_file)
    out.append(
        "platform: %s   confidence: %s (%.2f)"
        % (
            bug.platform or "unknown",
            confidence_label(result.confidence),
            result.confidence,
        )
    )
    out.append("-" * 74)

    if not result.suspects:
        out.append("No strong code suspect. Leaving triage to a human.")
    else:
        for i, s in enumerate(result.suspects, 1):
            c = s.candidate
            out.append('%d. %s  "%s"' % (i, s.label, c.commit.subject))
            out.append(
                "   %s - %s - score %.2f - %d line(s)"
                % (
                    c.commit.author_name,
                    c.commit.authored_at.date().isoformat(),
                    s.score,
                    c.lines_changed,
                )
            )
            for reason in s.reasons:
                out.append("     - %s" % reason)

    if result.excluded:
        out.append("")
        out.append("Excluded:")
        for note in dict.fromkeys(result.excluded):
            out.append("  - %s" % note)

    for note in result.notes:
        out.append("")
        out.append(note)
    return "\n".join(out)


def run_eval(repo: str, manifest_path: Path, config: dict) -> int:
    data = json.loads(manifest_path.read_text())
    truth = {b["bugId"]: b for b in data["bugs"]}
    bugs = fixture_source(manifest_path)

    hits_at_1 = 0
    hits_at_3 = 0
    rows = []

    for bug in bugs:
        result = analyse(repo, bug, config)
        expected = truth[bug.bug_id]["truePR"]
        predicted = [s.candidate.pr_number for s in result.suspects]
        at_1 = bool(predicted) and predicted[0] == expected
        at_3 = expected in predicted
        hits_at_1 += at_1
        hits_at_3 += at_3
        rows.append((bug.bug_id, expected, predicted, at_1, at_3, result.confidence))

    total = len(bugs)
    print("%-10s%-10s%-22s%-6s%-6s%s" % ("bug", "expected", "predicted", "P@1", "P@3", "conf"))
    print("-" * 66)
    for bug_id, expected, predicted, at_1, at_3, conf in rows:
        pred_str = ", ".join(str(p) for p in predicted) or "-"
        print(
            "%-10s#%-9d%-22s%-6s%-6s%.2f"
            % (
                bug_id,
                expected,
                pred_str,
                "yes" if at_1 else "NO",
                "yes" if at_3 else "NO",
                conf,
            )
        )
    print("-" * 66)
    print("precision@1: %d/%d (%.0f%%)" % (hits_at_1, total, 100.0 * hits_at_1 / total))
    print("precision@3: %d/%d (%.0f%%)" % (hits_at_3, total, 100.0 * hits_at_3 / total))
    return 0 if hits_at_1 == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="BugTrail suspect-PR attribution")
    parser.add_argument("--repo", default=str(REPO_ROOT))
    parser.add_argument("--config", default=str(HERE / "config.json"))
    parser.add_argument(
        "--manifest", default=str(REPO_ROOT / "fixtures" / "ground-truth.json")
    )
    parser.add_argument("--bug", help="analyse a single bug id")
    parser.add_argument("--eval", action="store_true", help="score against ground truth")
    parser.add_argument(
        "--comment", help="path to a CodeSage comment to use as the seed"
    )
    parser.add_argument("--reported-at", help="ISO timestamp the bug was reported")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    manifest = Path(args.manifest)

    if args.comment:
        comment_path = Path(args.comment)
        if args.reported_at:
            reported_at = datetime.fromisoformat(args.reported_at.replace("Z", "+00:00"))
        else:
            data = json.loads(manifest.read_text())
            reported_at = datetime.fromisoformat(
                data["reportedAt"].replace("Z", "+00:00")
            )
        bug_id = args.bug or comment_path.stem
        bug = codesage_source(comment_path, bug_id, reported_at)
        print(render(analyse(args.repo, bug, config)))
        return 0

    if args.eval:
        return run_eval(args.repo, manifest, config)

    bugs = fixture_source(manifest)
    if args.bug:
        bugs = [b for b in bugs if b.bug_id == args.bug]
        if not bugs:
            print("unknown bug id: %s" % args.bug, file=sys.stderr)
            return 2

    for bug in bugs:
        print(render(analyse(args.repo, bug, config)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
