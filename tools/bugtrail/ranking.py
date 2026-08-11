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

    return Suspect(candidate=candidate, score=round(score, 4), reasons=reasons)


def rank(candidates: list, reported_at: datetime, keywords: tuple, config: dict) -> tuple:
    scored = [_score(c, reported_at, keywords, config) for c in candidates]

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
