"""Resolve the owning team for a path from a CODEOWNERS file.

Owner routing matters more than blame: the engineer who last touched a file is
often not the right person to fix it, whereas the owning team always is.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Optional


def load_rules(repo: str) -> list:
    """Return (pattern, owners) in file order.

    GitHub honours the *last* matching rule, so order is preserved rather than
    sorted by specificity.
    """
    for candidate in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        path = Path(repo) / candidate
        if path.is_file():
            break
    else:
        return []

    rules = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        rules.append((parts[0], parts[1:]))
    return rules


def owners_for(repo: str, file_path: str) -> list:
    rules = load_rules(repo)
    matched = []
    for pattern, owners in rules:
        if _matches(pattern, file_path):
            matched = owners
    return matched


def _matches(pattern: str, file_path: str) -> bool:
    normalised = pattern.lstrip("/")
    if normalised.endswith("/"):
        return file_path.startswith(normalised)
    if fnmatch(file_path, normalised):
        return True
    # A bare directory pattern should still match everything beneath it.
    return file_path.startswith(normalised.rstrip("*"))


def matches_suggested_team(owners: list, suggested_team: Optional[str]) -> Optional[bool]:
    """Whether CODEOWNERS agrees with CodeSage's suggested team.

    Returns None when there is nothing to compare. A disagreement is a useful
    signal to lower confidence rather than an error.
    """
    if not owners or not suggested_team:
        return None
    team = suggested_team.lower().replace("-", "").replace("_", "")
    for owner in owners:
        normalised = owner.lstrip("@").lower().replace("-", "").replace("_", "")
        if team in normalised or normalised in team:
            return True
        # Compare trailing platform token, e.g. "PSDK-Android" vs "@x-android".
        tail = suggested_team.lower().rsplit("-", 1)[-1]
        if tail and tail in normalised:
            return True
    return False
