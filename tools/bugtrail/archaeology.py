"""Deterministic git archaeology: history, commit->PR mapping, diff analysis.

Nothing here calls a model. Every result is reproducible from the repository
alone, which is what makes the attribution defensible under review.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Iterable, Optional

from models import Candidate, Commit

_FIELD_SEP = "\x1f"

# GitHub's two merge formats: a squash merge appends "(#N)" to the subject,
# a merge commit uses "Merge pull request #N from owner/branch".
_SQUASH_PR = re.compile(r"\(#(\d+)\)\s*$")
_MERGE_PR = re.compile(r"^Merge pull request #(\d+)\b")

_COMMENT_PREFIXES = ("//", "/*", "*/", "*", "#", "<!--")


class GitError(RuntimeError):
    pass


def run_git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo] + list(args),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError("git %s failed: %s" % (" ".join(args), proc.stderr.strip()))
    return proc.stdout


def _parse_commit(line: str) -> Optional[Commit]:
    parts = line.split(_FIELD_SEP)
    if len(parts) < 6:
        return None
    sha, name, email, date, subject, parents = parts[:6]
    return Commit(
        sha=sha,
        author_name=name,
        author_email=email,
        authored_at=datetime.fromisoformat(date),
        subject=subject,
        parents=tuple(p for p in parents.split() if p),
    )


def commits_for_path(repo: str, path: str, limit: int = 60) -> list:
    """History of a single path, following renames.

    --follow accepts only one pathspec, which is why this is per-file.
    """
    fmt = _FIELD_SEP.join(["%H", "%an", "%ae", "%ad", "%s", "%P"])
    try:
        out = run_git(
            repo,
            "log",
            "--follow",
            "--date=iso-strict",
            "--format=%s" % fmt,
            "-%d" % limit,
            "--",
            path,
        )
    except GitError:
        return []
    parsed = (_parse_commit(ln) for ln in out.splitlines() if ln)
    return [c for c in parsed if c]


def module_files(repo: str, seed_path: str) -> list:
    """Sibling files in the seed file's directory, used to widen the search.

    CodeSage gives a starting point rather than a verdict, so the real cause can
    sit in a neighbouring file.
    """
    parent = str(PurePosixPath(seed_path).parent)
    if parent in ("", "."):
        return []
    try:
        out = run_git(repo, "ls-files", "--", parent)
    except GitError:
        return []
    return [p for p in out.splitlines() if p and p != seed_path]


def resolve_pr(repo: str, commit: Commit) -> Optional[int]:
    """Map a commit to its pull request number.

    Squash and merge commits carry the number in the subject. A commit merged via
    a merge commit does not, so walk forward along the ancestry path to the
    earliest merge that contains it.
    """
    m = _SQUASH_PR.search(commit.subject)
    if m:
        return int(m.group(1))
    m = _MERGE_PR.match(commit.subject)
    if m:
        return int(m.group(1))

    try:
        out = run_git(
            repo,
            "log",
            "--merges",
            "--ancestry-path",
            "--format=%s",
            "%s..HEAD" % commit.sha,
        )
    except GitError:
        return None

    merges = [ln for ln in out.splitlines() if ln]
    if not merges:
        return None
    # git log is newest-first, so the earliest containing merge is last.
    m = _MERGE_PR.match(merges[-1])
    return int(m.group(1)) if m else None


def _changed_lines(repo: str, sha: str, path: str) -> list:
    try:
        out = run_git(repo, "show", "--format=", "--unified=0", sha, "--", path)
    except GitError:
        return []
    lines = []
    for ln in out.splitlines():
        if ln.startswith(("+++", "---", "diff ", "index ", "@@")):
            continue
        if ln.startswith(("+", "-")):
            lines.append(ln[1:])
    return lines


def _is_code_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return not stripped.startswith(_COMMENT_PREFIXES)


def rename_similarity(repo: str, sha: str, path: str) -> Optional[int]:
    """Similarity score if this commit renamed a file into `path`.

    Rename detection needs both sides of the diff, so this runs without a
    pathspec. Left undetected, a rename looks like a whole-file delete plus add,
    which falsely matches every keyword in the bug report.
    """
    try:
        out = run_git(repo, "show", "--format=", "--name-status", "-M", sha)
    except GitError:
        return None
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].startswith("R") and parts[2] == path:
            digits = parts[0][1:]
            return int(digits) if digits.isdigit() else 100
    return None


def diff_analysis(repo: str, sha: str, path: str, keywords: Iterable) -> tuple:
    """Return (lines_changed, is_substantive, keyword_hits, is_rename).

    is_substantive is False for changes that only touch comments or blank lines,
    and for pure renames. That distinction separates a real behaviour change from
    a docs pass or a file move landing on the same path.
    """
    is_rename = (rename_similarity(repo, sha, path) or 0) >= 90
    changed = _changed_lines(repo, sha, path)
    code_lines = [ln for ln in changed if _is_code_line(ln)]

    if is_rename:
        return len(changed), False, (), True

    haystack = "\n".join(code_lines).lower()
    hits = tuple(k for k in keywords if k and k.lower() in haystack)
    return len(changed), bool(code_lines), hits, False


def is_generated(repo: str, path: str, globs: list, markers: list) -> bool:
    if any(fnmatch(path, g) for g in globs):
        return True
    try:
        head = run_git(repo, "show", "HEAD:%s" % path)
    except GitError:
        return False
    first_lines = "\n".join(head.splitlines()[:5])
    return any(m in first_lines for m in markers)


def is_bot(commit: Commit, patterns: list) -> bool:
    return any(re.search(p, commit.author_name, re.IGNORECASE) for p in patterns)


def is_revert(commit: Commit) -> bool:
    return commit.subject.startswith("Revert ")


def build_candidates(
    repo: str,
    seed_file: str,
    reported_at: datetime,
    keywords: Iterable,
    config: dict,
) -> tuple:
    """Collect and annotate every commit that could plausibly be the cause.

    Returns candidates plus a readable list of what was excluded and why, so the
    output can explain its own omissions.
    """
    keywords = tuple(keywords)
    excluded = []
    limit = int(config.get("historyLimit", 60))

    paths = [seed_file]
    if config.get("expandToModule", True):
        paths.extend(module_files(repo, seed_file))

    gen_globs = config.get("generatedPathGlobs", [])
    gen_markers = config.get("generatedMarkers", [])
    bot_patterns = config.get("botAuthorPatterns", [])

    candidates = []
    seen = set()

    for path in paths:
        if is_generated(repo, path, gen_globs, gen_markers):
            excluded.append("%s: generated source, not attributable" % path)
            continue

        for commit in commits_for_path(repo, path, limit=limit):
            key = (commit.sha, path)
            if key in seen:
                continue
            seen.add(key)

            if commit.is_merge:
                continue
            if commit.authored_at > reported_at:
                excluded.append(
                    "%s: landed after the bug was reported" % commit.short_sha
                )
                continue
            if is_bot(commit, bot_patterns):
                excluded.append(
                    "%s: bot author (%s)" % (commit.short_sha, commit.author_name)
                )
                continue
            if config.get("excludeReverts", True) and is_revert(commit):
                excluded.append("%s: revert commit" % commit.short_sha)
                continue

            lines_changed, substantive, hits, is_rename = diff_analysis(
                repo, commit.sha, path, keywords
            )
            candidates.append(
                Candidate(
                    commit=commit,
                    path=path,
                    is_seed_file=(path == seed_file),
                    pr_number=resolve_pr(repo, commit),
                    lines_changed=lines_changed,
                    is_substantive=substantive,
                    keyword_hits=hits,
                    is_rename=is_rename,
                )
            )

    return candidates, excluded
