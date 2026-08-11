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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from archaeology import build_candidates
from codesage import parse_comment
from localize import localize
from models import Result, TriageInput
from ranking import confidence_label, extract_keywords, rank
from report import render_report
from repo import CACHE_DIR, ensure_repo, resolve_ref

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]


def load_config(path: Path) -> dict:
    return json.loads(path.read_text())


def _timestamp(value: str) -> datetime:
    value = value.replace("Z", "+00:00")
    # Jira returns +0000; fromisoformat wants +00:00 before Python 3.11.
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    return datetime.fromisoformat(value)


def fixture_source(manifest_path: Path) -> list:
    """Load a bug manifest.

    Each bug may carry its own reportedAt, since mined historical bugs were
    reported at different times. A manifest-level reportedAt is the fallback.
    """
    data = json.loads(manifest_path.read_text())
    default_reported_at = (
        _timestamp(data["reportedAt"]) if "reportedAt" in data else None
    )

    bugs = []
    for bug in data["bugs"]:
        raw = bug.get("reportedAt")
        reported_at = _timestamp(raw) if raw else default_reported_at
        if reported_at is None:
            raise ValueError("%s has no reportedAt" % bug["bugId"])
        bugs.append(
            TriageInput(
                bug_id=bug["bugId"],
                title=bug["title"],
                seed_file=bug["seedFile"],
                reported_at=reported_at,
                platform=bug.get("platform"),
                display_title=bug.get("title"),
            )
        )
    return bugs


def codesage_source(
    comment_path: Path,
    bug_id: str,
    reported_at: datetime,
    title: str = None,
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
    body = triage.summary or triage.where_to_start
    if not title:
        first_sentence = body.split(". ")[0].strip()
        title = first_sentence[:100] + ("..." if len(first_sentence) > 100 else "")

    return TriageInput(
        bug_id=bug_id,
        title=body,
        seed_file=triage.seed_file,
        reported_at=reported_at,
        platform=triage.platform,
        triage=triage,
        display_title=title,
    )


_IOS_SUFFIXES = (".swift", ".m", ".mm", ".h")
_ANDROID_SUFFIXES = (".kt", ".kts", ".java")


def _is_managed_clone(path: str) -> bool:
    """Is this a clone we made, rather than a checkout the developer owns?"""
    try:
        return CACHE_DIR.resolve() in Path(path).resolve().parents
    except OSError:
        return False


def jira_source(bug_json: Path, repo: str, config: dict):
    """Build a seed straight from a Jira bug, with no upstream triage bot.

    Expects {key, summary, description, created} - exactly what the Jira read
    path returns. The starting-point file is derived here rather than taken on
    trust, which is what removes the CodeSage dependency.

    Only `created` is optional, and only because this entry point is also how a
    person tries the tool by hand. A file typed at a prompt to see what happens
    used to die on KeyError: 'created', which reads as the tool being broken
    rather than the input being short of one field nobody would guess at.
    """
    data = json.loads(bug_json.read_text())
    summary = data.get("summary") or ""
    description = data.get("description") or ""
    text = "%s\n\n%s" % (summary, description)

    ranked = localize(
        text,
        repo,
        limit=int(config.get("localizeLimit", 5)),
        rev=config.get("ref") or "HEAD",
    )
    if not ranked:
        return None, []

    seed = ranked[0].path
    lowered = seed.lower()
    platform = None
    if lowered.endswith(_ANDROID_SUFFIXES):
        platform = "android"
    elif lowered.endswith(_IOS_SUFFIXES):
        platform = "ios"

    bug = TriageInput(
        bug_id=data.get("key", bug_json.stem),
        title=text,
        seed_file=seed,
        # Now, when the field is absent: recency is measured against the moment
        # the bug was reported, and for a hand-written file that moment is this
        # one. Jira always supplies it, so this only affects manual runs.
        reported_at=(
            _timestamp(data["created"]) if data.get("created")
            else datetime.now(timezone.utc)
        ),
        platform=platform,
        display_title=summary,
    )
    return bug, ranked


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


def to_dict(result: Result) -> dict:
    """Machine-readable result, for CI or a future Jira adapter."""
    bug = result.bug
    return {
        "bugId": bug.bug_id,
        "title": bug.display_title or _headline(bug.title),
        "seedFile": bug.seed_file,
        "platform": bug.platform,
        "confidence": result.confidence,
        "confidenceLabel": confidence_label(result.confidence),
        "suspects": [
            {
                "pr": s.candidate.pr_number,
                "commit": s.candidate.commit.sha,
                "subject": s.candidate.commit.subject,
                "author": s.candidate.commit.author_name,
                "authorEmail": s.candidate.commit.author_email,
                "landedAt": s.candidate.commit.authored_at.isoformat(),
                "path": s.candidate.path,
                "linesChanged": s.candidate.lines_changed,
                "score": s.score,
                "reasons": s.reasons,
            }
            for s in result.suspects
        ],
        "excluded": list(dict.fromkeys(result.excluded)),
    }


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
    width = max([len(r[0]) for r in rows] + [8]) + 2
    header = "%-*s%-10s%-22s%-6s%-6s%s" % (
        width, "bug", "expected", "predicted", "P@1", "P@3", "conf"
    )
    print(header)
    print("-" * len(header))
    for bug_id, expected, predicted, at_1, at_3, conf in rows:
        pred_str = ", ".join(str(p) for p in predicted) or "-"
        print(
            "%-*s#%-9d%-22s%-6s%-6s%.2f"
            % (
                width,
                bug_id,
                expected,
                pred_str,
                "yes" if at_1 else "NO",
                "yes" if at_3 else "NO",
                conf,
            )
        )
    print("-" * len(header))
    print("precision@1: %d/%d (%.0f%%)" % (hits_at_1, total, 100.0 * hits_at_1 / total))
    print("precision@3: %d/%d (%.0f%%)" % (hits_at_3, total, 100.0 * hits_at_3 / total))
    return 0 if hits_at_1 == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="BugTrail suspect-PR attribution")
    parser.add_argument(
        "--repo",
        help="local path, clone URL, or owner/name. Defaults to config 'repo'. "
        "A remote is cloned once into ~/.cache/bugtrail and refreshed per run",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="refresh a local checkout before analysing; a stale clone cannot "
        "see the pull request that caused a recent bug",
    )
    parser.add_argument(
        "--ref",
        help="revision to analyse, e.g. origin/main. Defaults to the remote's "
        "default branch when fetching, since fetch does not move HEAD",
    )
    parser.add_argument("--config", default=str(HERE / "config.json"))
    parser.add_argument(
        "--manifest", default=str(REPO_ROOT / "fixtures" / "ground-truth.json")
    )
    parser.add_argument("--bug", help="analyse a single bug id")
    parser.add_argument("--eval", action="store_true", help="score against ground truth")
    parser.add_argument(
        "--comment", help="path to a CodeSage comment to use as the seed"
    )
    parser.add_argument(
        "--bug-json",
        help="path to a Jira bug {key, summary, description, created}; the seed "
        "file is derived by the localizer instead of taken from a triage bot",
    )
    parser.add_argument("--reported-at", help="ISO timestamp the bug was reported")
    parser.add_argument(
        "--report", action="store_true", help="render the full attribution report"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--history-limit",
        type=int,
        help="commits to inspect per file; lower this on large repositories",
    )
    parser.add_argument(
        "--no-module-expansion",
        action="store_true",
        help="consider only the seed file, not its module siblings",
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))
    args.repo = ensure_repo(
        args.repo or config.get("repo") or str(REPO_ROOT),
        quiet=args.json,
        fetch_local=args.fetch,
    )
    # Pin to the remote branch when asked to, and also whenever the repository is
    # one we provisioned ourselves.
    #
    # A checkout the developer owns is left on HEAD deliberately: they may be
    # sitting on a branch and analysing it is the point. But in the cache, HEAD
    # is just whichever commit the clone was taken at and never moves again,
    # because fetch updates remote-tracking refs and nothing else. That produced
    # answers from a snapshot weeks old - a file added since simply did not
    # exist, so the localizer picked the nearest wrong thing and the report
    # looked confidently absurd rather than empty.
    if args.ref or args.fetch or _is_managed_clone(args.repo):
        config["ref"] = resolve_ref(args.repo, args.ref)
    if args.history_limit:
        config["historyLimit"] = args.history_limit
    if args.no_module_expansion:
        config["expandToModule"] = False
    manifest = Path(args.manifest)

    if args.bug_json:
        bug, ranked = jira_source(Path(args.bug_json), args.repo, config)
        if bug is None:
            print("Could not locate any candidate file from the bug text.")
            return 0
        result = analyse(args.repo, bug, config)
        result.notes.append(
            "seed chosen by localizer: %s" % "; ".join(ranked[0].reasons)
        )
        if len(ranked) > 1:
            result.notes.append(
                "other candidates considered:\n"
                + "\n".join("  %.2f  %s" % (c.score, c.path) for c in ranked[1:])
            )
        if args.json:
            payload = to_dict(result)
            payload["localization"] = [
                {"path": c.path, "score": round(c.score, 3), "reasons": c.reasons}
                for c in ranked
            ]
            print(json.dumps(payload, indent=2))
        else:
            print(render_report(args.repo, result) if args.report else render(result))
        return 0

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
        # In production this is Jira's issue summary; here it comes from the manifest.
        title = None
        if manifest.is_file():
            for entry in json.loads(manifest.read_text())["bugs"]:
                if entry["bugId"] == bug_id:
                    title = entry.get("title")
                    break
        bug = codesage_source(comment_path, bug_id, reported_at, title=title)
        result = analyse(args.repo, bug, config)
        if args.json:
            print(json.dumps(to_dict(result), indent=2))
        else:
            print(render_report(args.repo, result) if args.report else render(result))
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
        result = analyse(args.repo, bug, config)
        if args.json:
            print(json.dumps(to_dict(result), indent=2))
        else:
            print(render_report(args.repo, result) if args.report else render(result))
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
