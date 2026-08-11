"""Find the files a bug report is talking about, without an upstream triage bot.

This replaces the only part of CodeSage that BugTrail actually consumed: the
starting-point file. Measured against 22 real triage comments, CodeSage supplied
no usable path on 8 of them and pointed at an unrelated subsystem on at least
one more, so depending on it capped both coverage and accuracy.

The approach is deliberately not a model. Bug reports written by engineers and QA
are full of literal code identifiers - exception types, class names, package
paths, filenames in backticks - and those identifiers exist in the repository.
Searching for them is deterministic, explainable, and reproducible by hand,
which is the same property that makes the archaeology step trustworthy.

Scoring is inverse document frequency. An identifier appearing in four files is
strong evidence; one appearing in four hundred is noise.
"""

from __future__ import annotations

import math
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

# Only these are searched, so a language missing here is invisible: the file can
# sit on the default branch containing the exact string from the bug report and
# still produce "no candidate file located", which reads as "nothing matched"
# rather than "never looked".
SOURCE_GLOBS = (
    "*.swift", "*.kt", "*.java", "*.m", "*.mm", "*.h", "*.tsx", "*.ts",
    "*.py", "*.js", "*.jsx", "*.go", "*.rb", "*.cs",
)
SOURCE_SUFFIXES = tuple(g[1:] for g in SOURCE_GLOBS)
_SUFFIX_RE = r"\.(%s)$" % "|".join(s[1:] for s in SOURCE_SUFFIXES)

# Paths that describe the tooling rather than the product it examines.
#
# Excluded because this triage agent lives in the same repository it analyses,
# and its own test file is the single densest collection of product vocabulary
# anywhere in the tree: it names preroll, ad-skip, greetings and every other
# symptom on purpose, as fixtures. Left in the corpus it wins nearly every
# search, and an ad-skip regression gets attributed to the bot's test file
# instead of to DefaultAdSkipManager.
#
# A tool blaming itself for a defect in the product is never the useful answer,
# even when it happens to be the tool that is broken.
EXCLUDED_PREFIXES = ("tools/bugtrail/", "tools/docs/")

# Test-file conventions across the languages searched. A fix almost always lands
# in the source file rather than in the test that covers it.
_TEST_RE = re.compile(
    r"(^|/)tests?/|(^|/)test_[^/]+$|_test\.[^/]+$|Tests?\.[^/]+$|\.(test|spec)\.[^/]+$"
)


def _is_searchable(path: str) -> bool:
    return path.endswith(SOURCE_SUFFIXES) and not path.startswith(EXCLUDED_PREFIXES)

# Identifiers common enough in this domain to carry no signal.
_STOP_IDENTIFIERS = {
    "HBOMax", "AndroidTV", "FireTV", "AppleTV", "IOException", "JSONObject",
    "NullPointerException", "RuntimeException", "URLSession", "UIView",
    "UIViewController", "ViewController", "PlayerSDK", "SDK", "API", "URL",
    "JSON", "HTTP", "HTTPS", "UI", "QA", "OS", "TV", "ID", "CTA", "USA",
    "DRM", "HLS", "DASH", "CDN", "VOD", "MLP", "PSDK", "ISDK",
}

# Declaration forms across the languages in play.
_DECL = r"(?:class|struct|enum|protocol|interface|extension|object|typealias|func|fun|val|var|let)"

# Prose that carries no location information. Domain nouns are deliberately
# absent: "spinner", "seek" and "caption" are exactly the words that locate code.
_STOP_WORDS = set("""
about actual added adding after again against all also always any app application are asked
back backgrounding been before being below both build but can case check click
close come could crash current customer device devices different does done during
each else even every expected fail failed fails first fixed following from
after get gets getting give given going happen happens has have here high home
how however issue issues into just keep known last launch like logs long look
made make many max more most move much must need needs new next node none not
note now observe observed once only open option other over page part pass please
press previous problem production provide qa release repro reproduce reproduced
result results same screen see seen should show showing shown since some start
started state steps still such take tested than that the their them then there
these they this those time times together too update use used user users using
version versions very via want was way well were what when where which while
who why will with within without work working would your
""".split())


@dataclass
class Candidate:
    path: str
    score: float = 0.0
    matched: Set[str] = field(default_factory=set)
    is_definition: bool = False
    reasons: List[str] = field(default_factory=list)


def extract_identifiers(text: str) -> List[str]:
    """Pull literal code identifiers out of prose.

    Only forms that a human would have copied from the code are kept; ordinary
    English words are useless for searching because they match everywhere.
    """
    found: Set[str] = set()

    # `backticked` tokens - the strongest signal, someone quoted the code.
    for m in re.finditer(r"`([A-Za-z_][\w./+-]{2,})`", text):
        found.add(m.group(1))

    # Filenames with a source extension.
    for m in re.finditer(r"\b([A-Za-z_]\w*\.(?:swift|kt|java|m|mm|h|tsx|ts))\b", text):
        found.add(m.group(1))

    # Dotted package or fully-qualified class paths.
    for m in re.finditer(r"\b((?:[a-z]\w*\.){2,}[A-Za-z]\w*)\b", text):
        found.add(m.group(1))

    # CamelCase with at least two humps: MediaItemResolverException.
    for m in re.finditer(r"\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b", text):
        found.add(m.group(1))

    # SCREAMING_SNAKE constants.
    for m in re.finditer(r"\b([A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+)\b", text):
        found.add(m.group(1))

    out = []
    for ident in found:
        bare = ident.split(".")[-1].split("/")[-1]
        stem = re.sub(r"\.(swift|kt|java|m|mm|h|tsx|ts)$", "", bare)
        if stem in _STOP_IDENTIFIERS or len(stem) < 4:
            continue
        out.append(ident)
    return sorted(out)


def _search_terms(identifiers: Sequence[str]) -> List[str]:
    """Search on the bare symbol; a path or filename will not appear verbatim."""
    terms: Set[str] = set()
    for ident in identifiers:
        bare = ident.split(".")[-1].split("/")[-1]
        terms.add(re.sub(r"\.(swift|kt|java|m|mm|h|tsx|ts)$", "", bare))
    return sorted(t for t in terms if len(t) >= 4 and t not in _STOP_IDENTIFIERS)


def _grep(
    repo: str, terms: Sequence[str], rev: str = "HEAD", max_per_file: int = 3
) -> Dict[str, Set[str]]:
    """One pass over the repository, returning term -> files containing it.

    `rev` matters for evaluation: searching at HEAD would let the very fix we
    are trying to predict leak into the evidence.
    """
    if not terms:
        return {}

    # -i because half the terms reaching here are prose, and prose_terms
    # lowercases everything it extracts. Matching case-sensitively meant a
    # lowercased term could only ever find lowercase code: a report saying
    # "neermita" would not find Neermita, and the miss looked like an absent
    # file rather than a mismatched search. The attribution loop below already
    # compares case-insensitively, so this makes the two halves agree.
    cmd = [
        "git", "-C", repo, "grep", "-I", "-F", "-w", "-i", "-n",
        "-m", str(max_per_file),
    ]
    for t in terms:
        cmd += ["-e", t]
    cmd += [rev, "--"] + list(SOURCE_GLOBS)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    # grep exits 1 when nothing matched, which is not an error here.
    if proc.returncode not in (0, 1):
        return {}

    lowered = {t.lower(): t for t in terms}
    hits: Dict[str, Set[str]] = defaultdict(set)
    for line in proc.stdout.split("\n"):
        # With a revision the format is rev:path:line:content.
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue
        _, path, _, content = parts
        if not _is_searchable(path):
            continue
        low = content.lower()
        for lt, term in lowered.items():
            if lt in low:
                hits[term].add(path)
    return hits


def _split_path_tokens(path: str) -> Set[str]:
    """Lowercase word tokens from a path, splitting CamelCase.

    ShortsPlayerControlsOverlay/ShortsPlayPauseButton.swift
      -> {shorts, player, controls, overlay, play, pause, button}
    """
    # Derived from SOURCE_SUFFIXES so a newly indexed language cannot end up
    # keeping its extension as a path token and matching on it.
    stem = re.sub(_SUFFIX_RE, "", path)
    words: Set[str] = set()
    for chunk in re.split(r"[/_\-.]", stem):
        for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", chunk):
            if len(w) >= 4:
                words.add(w.lower())
    return words


def _file_index(repo: str, rev: str) -> Dict[str, Set[str]]:
    out = subprocess.run(
        ["git", "-C", repo, "ls-tree", "-r", "--name-only", rev],
        capture_output=True, text=True,
    ).stdout
    index: Dict[str, Set[str]] = {}
    for path in out.split("\n"):
        path = path.strip()
        if _is_searchable(path):
            index[path] = _split_path_tokens(path)
    return index


def prose_terms(text: str) -> Set[str]:
    """Domain words from a bug report, once boilerplate is removed."""
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)}
    return {w for w in words if w not in _STOP_WORDS}


def _is_definition(repo: str, path: str, term: str, rev: str = "HEAD") -> bool:
    try:
        blob = subprocess.run(
            ["git", "-C", repo, "show", "%s:%s" % (rev, path)],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return False
    return re.search(r"\b%s\s+%s\b" % (_DECL, re.escape(term)), blob) is not None


def localize(
    text: str,
    repo: str,
    limit: int = 5,
    df_ceiling: int = 120,
    identifiers: Optional[Iterable[str]] = None,
    rev: str = "HEAD",
) -> List[Candidate]:
    """Rank repository files by how specifically the bug text points at them."""
    idents = list(identifiers) if identifiers is not None else extract_identifiers(text)
    terms = _search_terms(idents)
    hits = _grep(repo, terms, rev=rev)

    candidates: Dict[str, Candidate] = {}

    # Evidence 0: distinctive prose words, searched in file contents.
    #
    # The other two signals both miss the commonest report of all - someone
    # quoting a string they saw on screen. Identifiers only cover text shaped
    # like code, and the path index below only covers words that appear in a
    # file *name*, so a literal like "Neermita" sitting in the body of a file
    # matched nothing at all and the ticket came back "no candidate located".
    #
    # Rare words only. A word occurring all over the repository locates
    # nothing, and grepping it just adds noise for every file it touches.
    prose = prose_terms(text) - {t.lower() for t in terms}
    prose_hits = _grep(repo, sorted(prose), rev=rev)
    prose_total = sum(len(v) for v in prose_hits.values()) or 1
    for term, paths in prose_hits.items():
        df = len(paths)
        if df == 0 or df > max(3, prose_total // 4):
            continue
        # Scaled below an identifier match on purpose: an English word appearing
        # in a file is weaker evidence than a symbol quoted from it.
        weight = 0.6 * math.log(1 + prose_total / df)
        for path in paths:
            c = candidates.setdefault(path, Candidate(path=path))
            c.score += weight
            c.reasons.append("contains `%s`" % term)

    # Evidence 1: literal identifiers quoted from the code. High precision, but
    # only about a third of real reports contain any.
    total_files = sum(len(v) for v in hits.values()) or 1
    for term, paths in hits.items():
        df = len(paths)
        if df == 0 or df > df_ceiling:
            continue
        weight = math.log(1 + total_files / df)
        for path in paths:
            c = candidates.setdefault(path, Candidate(path=path))
            c.matched.add(term)
            c.score += weight

            stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if stem == term:
                c.score += 3.0 * weight
                c.is_definition = True
                c.reasons.append("file is named after `%s`" % term)

    # Evidence 2: domain words matched against file and directory names. Lower
    # precision, but it is the only signal on a prose-only report such as
    # "infinite spinner after seek".
    index = _file_index(repo, rev)
    if index:
        df_tok: Dict[str, int] = defaultdict(int)
        for tokens in index.values():
            for t in tokens:
                df_tok[t] += 1

        n_files = len(index)
        wanted = prose_terms(text)
        for path, tokens in index.items():
            shared = wanted & tokens
            if not shared:
                continue
            # Two ordinary words, or one distinctive one. Requiring two would
            # miss a small repository where "skip" names exactly one file.
            if len(shared) < 2 and min(df_tok[t] for t in shared) > 3:
                continue
            weight = sum(
                math.log(n_files / df_tok[t]) for t in shared if df_tok[t]
            )
            # Long paths accumulate tokens by chance; normalise for that.
            weight /= math.sqrt(len(tokens) or 1)
            if weight <= 0:
                continue
            c = candidates.setdefault(path, Candidate(path=path))
            c.score += weight
            c.reasons.append(
                "path matches %s" % ", ".join("`%s`" % t for t in sorted(shared)[:4])
            )

    # A fix almost always lands in the source file, not its test. Matched by
    # convention across all the languages searched: checking only for Swift and
    # Kotlin naming let every Python and TypeScript test through undamped, and a
    # test is a vocabulary magnet because it states the symptom in words.
    for path, c in candidates.items():
        if _TEST_RE.search(path):
            c.score *= 0.35

    if not candidates:
        return []

    # Confirm declarations only for the shortlist; reading blobs is expensive.
    shortlist = sorted(candidates.values(), key=lambda c: -c.score)[: limit * 4]
    for c in shortlist:
        if c.is_definition:
            continue
        for term in sorted(c.matched):
            if _is_definition(repo, c.path, term, rev=rev):
                c.score += 1.5
                c.is_definition = True
                c.reasons.append("declares `%s`" % term)
                break

    for c in shortlist:
        if c.matched:
            rare = sorted(c.matched, key=lambda t: len(hits[t]))[:3]
            c.reasons.insert(0, "mentions %s" % ", ".join("`%s`" % t for t in rare))

    return sorted(shortlist, key=lambda c: (-c.score, c.path))[:limit]
