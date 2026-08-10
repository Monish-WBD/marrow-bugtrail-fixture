# BugTrail

Turns a triaged bug report into **ranked suspect pull requests**, the **owning team**, the **suspect diff**, and a **drafted regression test** — deterministically, offline, with no dependencies beyond Python 3 and git.

---

## Problem statement

When a bug is filed, someone has to answer: *which change caused this, and who should look at it?*

Internally, **CodeSage** already answers the first half — it posts an AI triage comment suggesting priority, severity, component, team, and a starting-point file. What it does **not** do is close the loop to source control. An engineer still manually runs `git log` on the suggested file, reads through recent commits, works out which pull request each one came from, checks who authored it, and decides which is plausibly related. That is 10–20 minutes of mechanical archaeology per bug, repeated across every bug, on both platforms.

BugTrail automates exactly that gap. It **consumes** CodeSage's output rather than replacing it.

## What it does and does not do

**Does**

- Parse a CodeSage triage comment into a normalised seed (deterministically).
- Walk the seed file's history, following renames, plus its module siblings.
- Map each commit to its pull request, handling both squash and merge-commit strategies.
- Exclude candidates that cannot be the cause: bots, reverts, generated sources, and anything that landed after the report.
- Rank the survivors and explain every score in plain English.
- Resolve the owning team from CODEOWNERS, and flag disagreement with CodeSage's suggested team.
- Draft a regression test scaffold in the right framework for the platform.

**Does not**

- Re-implement CodeSage's root-cause analysis or its triage field suggestions.
- Claim certainty. Output is *"likely related changes"*, ranked with confidence, never *"X broke this"*.
- Write the assertion inside the drafted test — see [Drafted tests](#drafted-tests) for why that is deliberate.
- Talk to Jira or GitHub. Everything below runs against a local clone.

---

## Requirements

- Python **3.9+** (no third-party packages — standard library only)
- `git` on `PATH`

Deliberately dependency-free: no new libraries to vet, and it runs on any engineer's machine unchanged.

## Usage

From the repository root:

```bash
# Full report from a CodeSage comment
python3 tools/bugtrail/cli.py --comment fixtures/triage/SYN-002.txt --report

# Compact form
python3 tools/bugtrail/cli.py --comment fixtures/triage/SYN-002.txt

# Score the ranker against known culprits
python3 tools/bugtrail/cli.py --eval

# Analyse the bundled fixture bugs
python3 tools/bugtrail/cli.py --bug SYN-001

# Machine-readable output, for CI
python3 tools/bugtrail/cli.py --comment fixtures/triage/SYN-001.txt --json

# Tests
python3 -m unittest discover -s tools/bugtrail/tests

# Confirm a drafted regression test isolates the suspect commit
tools/bugtrail/verify/verify.sh
```

On a large repository, bound the search or PR resolution walks too much history:

```bash
python3 tools/bugtrail/cli.py --comment <file> --repo <path> \
  --history-limit 12 --no-module-expansion --reported-at 2026-07-02T04:00:00Z
```

### As a Cursor skill

`.cursor/skills/bugtrail/SKILL.md` wraps this workflow so the agent runs the engine, fills the drafted test's assertion, verifies it fails for the right reason, and composes the report. The skill is explicit about the split: the engine decides *which PR*, the agent handles only the judgement calls, and it must never re-rank suspects from intuition.

Useful flags: `--repo` to point at a different clone, `--config` for alternative tuning, `--manifest` to score against a different bug set, `--reported-at` to override the report timestamp.

## Configuration

All tuning lives in `config.json`, never in code, so another team adopts this by editing a file rather than editing logic:

| Key | Meaning |
| --- | --- |
| `weights` | Relative influence of recency, keyword overlap, seed-file match, substantiveness |
| `halfLifeDays` | Recency decay half-life |
| `moduleWeight` | Score multiplier for module siblings vs the seed file itself |
| `cosmeticPenalty` | Penalty applied to comment-only and rename-only changes |
| `minConfidence` | Below this, report no suspect rather than guessing |
| `maxSuspects` | How many candidates to show |
| `botAuthorPatterns` | Authors treated as automation |
| `generatedPathGlobs`, `generatedMarkers` | How generated sources are recognised |
| `excludeReverts` | Whether revert commits are dropped |

## Validation

```
bug       expected  predicted    P@1   P@3
SYN-001   #5        5, 2, 4      yes   yes
SYN-002   #11       11, 3        yes   yes
SYN-003   #4        4, 5, 2      yes   yes
precision@1: 3/3      precision@3: 3/3
```

Plus 15 unit tests covering the parser, ADF flattening, and every ranking rule.

**Read this number honestly.** Three cases, hand-built, scored by a ranker tuned against them — it is a regression guard, not evidence of real-world accuracy. It exists so that a change to the ranker cannot silently make attribution worse.

The real number requires historical bugs whose fix PR is already known. `--manifest` accepts any file in the shape of `fixtures/ground-truth.json`, so pointing it at a set of real closed bugs produces a defensible precision figure without touching the engine.

The fixture deliberately includes traps, so a naive implementation fails: a comment-only change to the same file the day after the real culprit; a rename; a revert; a bot commit; a generated file; a hotfix pushed straight to main with no PR; and a culprit that predates a rename and is unreachable without `--follow`.

### Attribution is mechanically confirmed, not asserted

A regression test that passes proves nothing. `tools/bugtrail/verify/verify.sh` compiles the same assertion against two revisions and compares:

```
at HEAD (bug present, expect FAIL)
  FAIL  a skippable preroll was reported as not skippable
  => as expected: the test catches the bug

at 5ccb5dc^ (before the suspect PR, expect PASS)
  PASS  a skippable preroll remains skippable on the ad-free tier
  => as expected: the behaviour was intact here

CONFIRMED: behaviour changed at the suspect commit. Attribution holds.
```

Both outcomes together are what confirms the attribution: the behaviour changed *at that commit* and nowhere else. If the assertion fails at both revisions, the assertion is wrong rather than the code — and the script says so.

Needs only `swiftc`, no Xcode project or test target. The Kotlin case is verified by inspection, since this fixture has no Gradle setup.

### Deriving ground truth from history

`mine_szz.py` builds a bug manifest from git history alone — no issue tracker, no API, no credentials:

```bash
# Derive ground truth, then score against it. Both run on this repo alone.
python3 tools/bugtrail/mine_szz.py --repo . --scan 200 --out fixtures/mined-ground-truth.json
python3 tools/bugtrail/cli.py --eval --manifest fixtures/mined-ground-truth.json --repo .
```

Use `--require-ticket` on repositories whose fix commits carry ticket ids, to filter out incidental fixes.

An issue tracker records which PR *fixed* a bug, never which one caused it, so attribution ground truth does not exist as a field anywhere. The Śliwerski–Zimmermann–Zeller approach recovers it from the repository: take a commit that fixed a bug, find the lines it changed, blame those lines at the fix's parent, and the commit that last wrote them is the likely cause.

**This is derived ground truth, not human-verified.** Blame can land on a reformat, or on the commit that moved code rather than the one that broke it. Only single-file fixes are used, since multi-file fixes make the causing change ambiguous — which biases the sample toward simpler bugs. Any figure reported from this should carry those caveats, ideally alongside a hand-checked sample.

Run on this fixture it derives only two bugs, and in both the blamed cause is the file's creation commit — an artefact of a short history where nothing else precedes the fix. So it demonstrates that the mechanism works; it is **not** an accuracy measurement. A meaningful figure needs a repository with years of history and enough single-file fixes to make the sample size worth quoting.

## Drafted tests

The scaffold is deterministic: target path, framework (XCTest or JUnit, chosen from the seed file's extension), class name, and the symbol under test are all derived from the suspect diff. When the change sits inside an existing function body — the common case — the enclosing function is found by scanning upward from the hunk's start line.

The **assertion is intentionally left as a `TODO`**. Writing it requires knowing what the code was *supposed* to do, which is a judgement call, not a transformation. That is the part a model or an engineer should fill in, and the part nobody should merge unreviewed. A generator that emitted confident-looking assertions would demo well and mislead on the first real bug.

## Security and IP

- **No network calls.** Reads a local git clone and local files only.
- **No credentials, tokens, or secrets** are read, stored, or required.
- **No third-party dependencies**, so nothing new to vet.
- **No sensitive data in this repository.** Every fixture is synthetic: invented bug text, invented authors, invented file paths. `.gitignore` excludes `fixtures/codesage/` so that real triage comments used for local testing cannot be committed by accident.
- **Sanitisation is a prerequisite for the Jira integration**, not an afterthought: stack traces and logs must be stripped of PII, tokens, and customer identifiers before any model sees them.
- **Attribution is framed as suggestion.** Every report states that suspects are ranked suggestions and must be verified before assigning.

## Expected impact

| | Today | With BugTrail |
| --- | --- | --- |
| Find candidate changes for a bug | 10–20 min of manual `git log` | seconds |
| Identify the owning team | tribal knowledge | resolved from CODEOWNERS |
| Cross-platform consistency | each platform triaged by hand | one tool, both repos |
| Starting point for the fix | a blank editor | suspect diff plus a test scaffold |

Worth measuring once deployed: median time to first triage, share of bugs where the accepted culprit appeared in the top three, and how often the drafted test survives into the merged fix.

## Limitations

- Keyword matching is substring-based, so short tokens can match loosely (`roll` matches `preroll`).
- Module expansion covers the seed file's immediate directory, not the whole module tree.
- Commit-to-PR mapping relies on GitHub's message conventions; a repository using different merge messages needs the GitHub API path instead.
- Bugs whose cause is configuration, content, or a backend change have no code culprit — the confidence threshold is what stops the tool inventing one.
- Precision is unmeasured against real bugs. See [Validation](#validation).

## Next steps

1. Score against historical bugs with known fix PRs to get a real precision figure.
2. Jira read path: fetch the issue and its CodeSage comment via the approved Atlassian integration (`parse_adf_comment` already handles the ADF body format).
3. Jira write path: post one idempotent comment per issue, updating rather than duplicating.
4. Trigger: a JQL poller needs no admin rights and no infrastructure; a webhook is the hardened version.
5. Let a model fill the drafted test's assertion, still gated on human review.
6. Feedback signal (a label such as `bugtrail-correct` / `bugtrail-wrong`) to collect ground truth continuously and tune the weights on real data.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the component and data-flow design.
