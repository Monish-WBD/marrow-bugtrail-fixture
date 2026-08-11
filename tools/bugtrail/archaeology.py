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


def parse_timestamp(value: str) -> datetime:
    """Parse a git ISO timestamp.

    Real histories contain both "+00:00" and "Z" offsets, and Python 3.9's
    fromisoformat rejects the latter.
    """
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def _parse_commit(line: str) -> Optional[Commit]:
    parts = line.split(_FIELD_SEP)
    if len(parts) < 6:
        return None
    sha, name, email, date, subject, parents = parts[:6]
    try:
        authored_at = parse_timestamp(date)
    except ValueError:
        return None
    return Commit(
        sha=sha,
        author_name=name,
        author_email=email,
        authored_at=authored_at,
        subject=subject,
        parents=tuple(p for p in parents.split() if p),
    )


def commits_for_path(repo: str, path: str, limit: int = 60, rev: str = "HEAD") -> list:
    """History of a single path, following renames.

    Returns (commit, path_at_that_commit) pairs. The historical path matters:
    a commit made before a rename does not contain today's filename, so
    diffing it against the current path yields nothing and the change would
    wrongly look empty.

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
            "--name-status",
            "-%d" % limit,
            rev,
            "--",
            path,
        )
    except GitError:
        return []

    pairs = []
    current = None
    for line in out.splitlines():
        if _FIELD_SEP in line:
            commit = _parse_commit(line)
            if commit:
                current = commit
                pairs.append([commit, path])
            continue
        if current is None or "\t" not in line:
            continue
        parts = line.split("\t")
        status = parts[0]
        # A rename reports both names; the second is the path at this commit.
        historical = parts[2] if status.startswith(("R", "C")) and len(parts) >= 3 else parts[1]
        if pairs and pairs[-1][0] is current:
            pairs[-1][1] = historical
            current = None

    return [(c, p) for c, p in pairs]


def module_files(repo: str, seed_path: str, rev: str = "HEAD") -> list:
    """Sibling files in the seed file's directory, used to widen the search.

    The starting point is a hint rather than a verdict, so the real cause can sit
    in a neighbouring file.

    Reads the tree at `rev` rather than `git ls-files`, which would report the
    index and working tree and therefore include uncommitted local files.
    """
    parent = str(PurePosixPath(seed_path).parent)
    if parent in ("", "."):
        return []
    try:
        out = run_git(repo, "ls-tree", "-r", "--name-only", rev, "--", parent)
    except GitError:
        return []
    return [p for p in out.splitlines() if p and p != seed_path]


def resolve_pr(repo: str, commit: Commit, rev: str = "HEAD") -> Optional[int]:
    """Map a commit to its pull request number.

    Squash and merge commits carry the number in the subject. A commit merged via
    a merge commit does not, so walk forward along the ancestry path to the
    earliest merge that contains it.

    The walk has to end at the same revision everything else is analysed
    against. Against HEAD it silently finds nothing for any commit that has been
    fetched but not checked out, and the report degrades to a bare SHA - which
    reads as "no pull request" rather than "looked in the wrong place".
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
            "%s..%s" % (commit.sha, rev),
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


def diff_hunk(repo: str, sha: str, path: str, max_lines: int = 12) -> list:
    """The changed lines of a commit, prefixed, for display in the report."""
    try:
        out = run_git(repo, "show", "--format=", "--unified=1", sha, "--", path)
    except GitError:
        return []
    lines = []
    for ln in out.splitlines():
        if ln.startswith(("+++", "---", "diff ", "index ", "new file", "deleted file")):
            continue
        if ln.startswith(("@@", "+", "-", " ")):
            lines.append(ln)
        if len(lines) >= max_lines:
            lines.append("... (truncated)")
            break
    return lines


def merge_strategy(commit: Commit) -> str:
    if _SQUASH_PR.search(commit.subject):
        return "squash merge"
    if _MERGE_PR.match(commit.subject):
        return "merge commit"
    return "merge commit"


_DECLARATION = re.compile(r"\b(?:func|fun|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def changed_symbols(repo: str, sha: str, path: str) -> list:
    """Function names a commit touched.

    Declarations in the diff are the easy case. More often a change sits inside
    an existing function body and no declaration appears at all, so fall back to
    the enclosing function found by scanning upward from the hunk's start line.
    """
    found = []
    for line in _changed_lines(repo, sha, path):
        for name in _DECLARATION.findall(line):
            if name not in found:
                found.append(name)
    if found:
        return found

    return _enclosing_symbols(repo, sha, path)


def _enclosing_symbols(repo: str, sha: str, path: str) -> list:
    try:
        diff = run_git(repo, "show", "--format=", "--unified=0", sha, "--", path)
        blob = run_git(repo, "show", "%s:%s" % (sha, path))
    except GitError:
        return []

    lines = blob.splitlines()
    found = []
    for header in diff.splitlines():
        m = _HUNK_HEADER.match(header)
        if not m:
            continue
        start = int(m.group(1))
        for idx in range(min(start, len(lines)) - 1, -1, -1):
            match = _DECLARATION.search(lines[idx])
            if match:
                name = match.group(1)
                if name not in found:
                    found.append(name)
                break
    return found


def is_generated(repo: str, path: str, globs: list, markers: list, rev: str = "HEAD") -> bool:
    if any(fnmatch(path, g) for g in globs):
        return True
    try:
        head = run_git(repo, "show", "%s:%s" % (rev, path))
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
    rev = config.get("ref") or "HEAD"

    paths = [seed_file]
    if config.get("expandToModule", True):
        paths.extend(module_files(repo, seed_file, rev=rev))

    gen_globs = config.get("generatedPathGlobs", [])
    gen_markers = config.get("generatedMarkers", [])
    bot_patterns = config.get("botAuthorPatterns", [])

    candidates = []
    seen = set()

    for path in paths:
        if is_generated(repo, path, gen_globs, gen_markers, rev=rev):
            excluded.append("%s: generated source, not attributable" % path)
            continue

        for commit, historical_path in commits_for_path(repo, path, limit=limit, rev=rev):
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
                repo, commit.sha, historical_path, keywords
            )
            candidates.append(
                Candidate(
                    commit=commit,
                    path=path,
                    is_seed_file=(path == seed_file),
                    pr_number=resolve_pr(repo, commit, rev),
                    lines_changed=lines_changed,
                    is_substantive=substantive,
                    keyword_hits=hits,
                    is_rename=is_rename,
                )
            )

    return candidates, excluded
