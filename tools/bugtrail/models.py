"""Core data types shared by the archaeology and ranking stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Commit:
    sha: str
    author_name: str
    author_email: str
    authored_at: datetime
    subject: str
    parents: tuple

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


@dataclass
class Candidate:
    """A commit that touched a file in the candidate set, with diff analysis."""

    commit: Commit
    path: str
    is_seed_file: bool
    pr_number: Optional[int]
    lines_changed: int
    is_substantive: bool
    keyword_hits: tuple
    is_rename: bool = False


@dataclass
class Suspect:
    candidate: Candidate
    score: float
    reasons: list = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.candidate.pr_number is not None:
            return "PR #%d" % self.candidate.pr_number
        return "commit %s (no PR)" % self.candidate.commit.short_sha


@dataclass
class TriageInput:
    """Normalised triage seed, produced by any source (fixture or Jira)."""

    bug_id: str
    title: str
    seed_file: str
    reported_at: datetime
    platform: Optional[str] = None
    keywords: tuple = ()
    # The originating CodeSage triage, when the seed came from one.
    triage: object = None
    # Jira's issue summary. Absent from CodeSage comments, so it is supplied
    # separately and falls back to the first sentence of the triage summary.
    display_title: Optional[str] = None


@dataclass
class Result:
    bug: TriageInput
    suspects: list
    confidence: float
    excluded: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def has_strong_suspect(self) -> bool:
        return bool(self.suspects)
