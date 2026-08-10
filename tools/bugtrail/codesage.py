"""Parse a CodeSage triage comment into a normalised seed.

Deliberately regex-based and deterministic: no model is involved in reading the
comment, so a parse either succeeds exactly or fails loudly.

Two rules worth keeping:
  - Fail closed. If the "Starting Point" file is absent we return None rather
    than guessing, because a wrong seed produces confidently wrong attribution.
  - Treat the format as a contract. The sample comment is pinned in the tests so
    that an upstream format change breaks CI instead of silently degrading.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

MARKER = "AI Triage Suggestion"

_PRIORITY = re.compile(r"^Suggested Priority:\s*(\S+)", re.MULTILINE)
_SEVERITY = re.compile(r"^Suggested Severity:\s*(\S+)", re.MULTILINE)
_COMPONENT = re.compile(r"^Suggested Component:\s*(.+?)\s*$", re.MULTILINE)
_TEAM = re.compile(r"^Suggested Team:\s*(.+?)\s*$", re.MULTILINE)
_FILE = re.compile(r"^\s*[-*]\s*File:\s*(.+?)\s*$", re.MULTILINE)
_WHERE = re.compile(r"^\s*[-*]\s*Where to start:\s*(.+?)\s*$", re.MULTILINE)
_SUMMARY = re.compile(
    r"^Summary:\s*\n(.*?)(?=^\s*(?:Thought Logic:|Starting Point:)\s*$)",
    re.MULTILINE | re.DOTALL,
)

_ANDROID_SUFFIXES = (".kt", ".kts", ".java")
_IOS_SUFFIXES = (".swift", ".m", ".mm", ".h")

# ADF nodes that should end up on their own line when flattened.
_BLOCK_TYPES = {
    "paragraph",
    "heading",
    "listItem",
    "blockquote",
    "codeBlock",
    "panel",
    "rule",
    "tableRow",
}


@dataclass
class CodeSageTriage:
    seed_file: str
    summary: str = ""
    where_to_start: str = ""
    suggested_priority: Optional[str] = None
    suggested_severity: Optional[str] = None
    suggested_component: Optional[str] = None
    suggested_team: Optional[str] = None

    @property
    def platform(self) -> Optional[str]:
        lowered = self.seed_file.lower()
        if lowered.endswith(_ANDROID_SUFFIXES):
            return "android"
        if lowered.endswith(_IOS_SUFFIXES):
            return "ios"
        return None

    @property
    def is_regression(self) -> bool:
        return bool(re.search(r"\bregression\b", self.summary, re.IGNORECASE))


def flatten_adf(node) -> str:
    """Flatten Atlassian Document Format into plain text.

    Jira's v3 API returns comment bodies as ADF JSON rather than text, so the
    regexes above would never match without this step.
    """
    if isinstance(node, str):
        node = json.loads(node)

    parts: list = []

    def walk(n) -> None:
        if isinstance(n, list):
            for item in n:
                walk(item)
            return
        if not isinstance(n, dict):
            return

        node_type = n.get("type")
        if node_type == "text":
            parts.append(n.get("text", ""))
            return
        if node_type == "hardBreak":
            parts.append("\n")
            return

        walk(n.get("content", []))

        if node_type in _BLOCK_TYPES:
            parts.append("\n")

    walk(node)
    text = "".join(parts)
    # Collapse the runs of blank lines that block nesting produces.
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _first(pattern: re.Pattern, text: str) -> Optional[str]:
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def parse_comment(text: str) -> Optional[CodeSageTriage]:
    """Parse a plain-text CodeSage comment. Returns None if it is not one."""
    if MARKER not in text:
        return None

    seed_file = _first(_FILE, text)
    if not seed_file:
        return None

    summary_match = _SUMMARY.search(text)
    summary = summary_match.group(1).strip() if summary_match else ""

    return CodeSageTriage(
        seed_file=seed_file,
        summary=summary,
        where_to_start=_first(_WHERE, text) or "",
        suggested_priority=_first(_PRIORITY, text),
        suggested_severity=_first(_SEVERITY, text),
        suggested_component=_first(_COMPONENT, text),
        suggested_team=_first(_TEAM, text),
    )


def parse_adf_comment(body) -> Optional[CodeSageTriage]:
    return parse_comment(flatten_adf(body))
