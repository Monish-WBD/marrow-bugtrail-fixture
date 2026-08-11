"""Scoring: turn annotated candidates into a ranked, explained suspect list."""

from __future__ import annotations

import math
import re
from datetime import datetime

from models import Candidate, Suspect

_STOPWORDS = {
    "with", "when", "that", "this", "from", "have", "does", "should", "would",
    "there", "their", "which", "while", "after", "before", "being", "been",
    "user", "users", "issue", "bug", "regression", "during", "shown", "show",
    "displayed", "missing", "button", "screen", "again", "still", "only",
    "tier", "mode", "into", "over", "under", "app", "returns", "return",
    # Prose connectives and narration common in generated triage summaries.
    "where", "even", "though", "therefore", "however", "because", "appears",
    "rooted", "start", "starting", "investigation", "investigate", "determine",
    "check", "look", "review", "whether", "being", "these", "those", "some",
    "must", "will", "also", "than", "then", "such", "same", "here", "each",
    "about", "could", "since", "used", "using", "made", "make", "makes",
    "cause", "caused", "causes", "reported", "report", "expected", "actual",
    "instead", "without", "reaching", "longer", "renders", "reports", "note",
}

# A whole triage paragraph yields dozens of tokens, which dilutes the keyword
# score. Keep the earliest terms, since summaries lead with the symptom.
_MAX_KEYWORDS = 12


def extract_keywords(title: str, extra: tuple = (), limit: int = _MAX_KEYWORDS) -> tuple:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9]+", title.lower())
    picked = [w for w in words if len(w) >= 4 and w not in _STOPWORDS]
    merged = list(dict.fromkeys(picked + [e.lower() for e in extra]))
    return tuple(merged[:limit])


def _is_relevant(candidate: Candidate) -> bool:
    """Is there any evidence tying this change to the report at all?

    Touching the starting-point file counts, and so does mentioning the
    reporter's vocabulary. A change with neither is only "nearby and recent".
    """
    return candidate.is_seed_file or bool(candidate.keyword_hits)


def _recency(candidate: Candidate, reported_at: datetime, half_life_days: float) -> float:
    delta = (reported_at - candidate.commit.authored_at).total_seconds()
    age_days = max(delta, 0) / 86400
    return math.exp(-age_days / max(half_life_days, 1e-6))


def _score(
    candidate: Candidate,
    reported_at: datetime,
    keywords: tuple,
    config: dict,
) -> Suspect:
    weights = config.get("weights", {})
    w_recency = float(weights.get("recency", 0.35))
    w_keyword = float(weights.get("keyword", 0.35))
    w_seed = float(weights.get("seedFile", 0.20))
    w_subst = float(weights.get("substantive", 0.10))
    module_weight = float(config.get("moduleWeight", 0.5))
    cosmetic_penalty = float(config.get("cosmeticPenalty", 0.75))

    recency = _recency(candidate, reported_at, float(config.get("halfLifeDays", 30)))
    keyword = min(len(candidate.keyword_hits) / len(keywords), 1.0) if keywords else 0.0
    seed = 1.0 if candidate.is_seed_file else module_weight
    subst = 1.0 if candidate.is_substantive else 0.0

    score = w_recency * recency + w_keyword * keyword + w_seed * seed + w_subst * subst

    reasons = []
    if candidate.is_seed_file:
        reasons.append("modifies the starting-point file")
    else:
        reasons.append("same module (%s)" % candidate.path)

    if candidate.keyword_hits:
        reasons.append(
            "changed code mentions "
            + ", ".join("'%s'" % k for k in candidate.keyword_hits)
        )

    days = int((reported_at - candidate.commit.authored_at).total_seconds() // 86400)
    if days == 0:
        reasons.append("landed the same day the bug was reported")
    else:
        reasons.append(
            "landed %d day%s before the report" % (days, "" if days == 1 else "s")
        )

    if candidate.is_rename:
        score *= 1.0 - cosmetic_penalty
        reasons.append("file rename, so no behaviour change")
    elif not candidate.is_substantive:
        score *= 1.0 - cosmetic_penalty
        reasons.append("comment or whitespace only, so unlikely to change behaviour")

    if not _is_relevant(candidate):
        # Recency and substantiveness are worth 0.45 between them, and neither
        # says anything about whether a change relates to the bug. Without this,
        # any sizeable commit landing anywhere in the module this week outranks
        # the one commit that edited the starting-point file - which is how a
        # greeting tweak ends up blamed for an ad-skip defect.
        score *= 1.0 - float(config.get("unrelatedModulePenalty", 0.6))
        reasons.append(
            "different file in the same module, and nothing in it matches the report"
        )

    return Suspect(candidate=candidate, score=round(score, 4), reasons=reasons)


def _boost_latest_change(scored: list, config: dict) -> None:
    """Favour the newest real change to this code.

    Keyword overlap alone rewards whichever commit introduced the most
    vocabulary, which is nearly always the one that created the file: it
    mentions every term because it wrote every line. So a report of "this used
    to say X and now says Y" gets attributed to the author who first wrote X,
    rather than whoever changed it to Y.

    Most bugs worth attributing are regressions, and for a regression the last
    substantive change to the code is the better suspect. Renames and
    comment-only edits are skipped, or a whitespace pass would inherit the
    blame simply by being last.
    """
    bonus = float(config.get("latestChangeBonus", 0.10))
    if bonus <= 0:
        return

    newest = None
    for suspect in scored:
        c = suspect.candidate
        if c.is_rename or not c.is_substantive or not _is_relevant(c):
            continue
        if newest is None or (
            c.commit.authored_at > newest.candidate.commit.authored_at
        ):
            newest = suspect

    if newest is not None:
        # Clamped, because the score doubles as the confidence shown on the
        # ticket. "Confidence 1.09" invites the question of what 1.0 meant.
        newest.score = round(min(newest.score + bonus, 1.0), 4)
        newest.reasons.append("most recent substantive change to this code")


def rank(candidates: list, reported_at: datetime, keywords: tuple, config: dict) -> tuple:
    scored = [_score(c, reported_at, keywords, config) for c in candidates]
    _boost_latest_change(scored, config)

    # One entry per pull request: several commits can belong to the same PR.
    best = {}
    for suspect in scored:
        pr = suspect.candidate.pr_number
        key = pr if pr is not None else "sha:%s" % suspect.candidate.commit.sha
        if key not in best or suspect.score > best[key].score:
            best[key] = suspect

    ordered = sorted(best.values(), key=lambda s: s.score, reverse=True)

    min_confidence = float(config.get("minConfidence", 0.35))
    max_suspects = int(config.get("maxSuspects", 3))

    confidence = ordered[0].score if ordered else 0.0
    if confidence < min_confidence:
        return [], confidence
    return ordered[:max_suspects], confidence


def confidence_label(confidence: float) -> str:
    if confidence >= 0.75:
        return "High"
    if confidence >= 0.55:
        return "Medium"
    return "Low"
