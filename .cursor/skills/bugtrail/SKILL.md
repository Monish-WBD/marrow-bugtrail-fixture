---
name: bugtrail
description: Attributes a triaged bug to the pull requests, commits and authors that likely caused it, resolves the owning team from CODEOWNERS, and writes a failing regression test. Use when a bug needs triaging to a change, when a CodeSage triage comment names a starting-point file, or when the user asks which PR or commit caused a bug, who should look at it, or for a regression test that reproduces it.
---

# BugTrail

Turns a triaged bug into: **which PRs likely caused it**, **who owns the code**, and **a failing regression test**.

CodeSage already answers *where to look*. This closes the loop to source control, which is otherwise 10–20 minutes of manual `git log` per bug.

## Division of labour

This matters more than anything else here.

| Deterministic — never guess | Agent judgement — that's you |
| --- | --- |
| Which commits touched the file | Filling the regression test's assertion |
| Which PR each commit came from | Phrasing the summary for humans |
| Exclusions (bots, reverts, generated) | Deciding whether a suspect is plausible |
| The scores and their ranking | Explaining *why* the change broke behaviour |

**Never override the engine's ranking from intuition.** If you disagree with it, say so in prose and leave the ranking intact — a reviewer must be able to reproduce every suspect with `git log` by hand.

## Workflow

```
- [ ] 1. Get the triage seed
- [ ] 2. Run the engine
- [ ] 3. Sanity-check the top suspect
- [ ] 4. Fill the regression test assertion
- [ ] 5. Verify the test fails for the right reason
- [ ] 6. Compose the report
```

### 1. Get the triage seed

A CodeSage comment (from Jira, or saved to a file). The parser needs the `- File:` line; everything else is optional.

Save real comments under `fixtures/codesage/` — it is gitignored, so internal paths never get committed.

### 2. Run the engine

```bash
python3 tools/bugtrail/cli.py --comment <comment-file> --report
```

Large repositories need bounding, or PR resolution walks too much history:

```bash
python3 tools/bugtrail/cli.py --comment <file> --repo <path> \
  --history-limit 12 --no-module-expansion --reported-at 2026-07-02T04:00:00Z
```

Use `--json` when the output feeds another step.

### 3. Sanity-check the top suspect

Read the printed `suspect change` hunk and ask: **could this diff plausibly produce the reported symptom?**

If yes, continue. If the top suspect looks wrong, say so explicitly and explain which of the printed reasons is misleading. Do not silently re-rank.

If confidence is below threshold the engine reports no suspect. That is a valid outcome — do not manufacture one.

### 4. Fill the regression test assertion

The engine drafts a scaffold with the correct path, framework, class name, and symbol, and leaves the assertion as a `TODO`. Replace it with a test that encodes **the behaviour the bug says was lost** — not the current behaviour.

Write the assertion from the bug report, not from the code. If you read the implementation first you will encode the bug.

### 5. Verify the test fails for the right reason

A regression test that passes proves nothing.

```bash
tools/bugtrail/verify/verify.sh
```

This compiles the test against `HEAD` (expects **fail**) and against the commit before the suspect PR (expects **pass**). Both outcomes together confirm attribution.

If the test fails at both revisions, the assertion is wrong, not the code.

### 6. Compose the report

```markdown
**Likely related changes** — confidence: <High|Medium|Low>

1. <PR #N> "<subject>" — <author>, merged <date>
   Why: <reasons from the engine, verbatim>

Suspect change:
<diff hunk>

Owner (CODEOWNERS): <team> <agrees|does not match> CodeSage's suggested team

Regression test: <path> — fails at HEAD, passes before <PR #N>

_Suspect changes are ranked suggestions, not attributions of fault. Verify before assigning._
```

## Rules

- Say **"likely related changes"**, never who broke it. Attribution errors are cheap; blaming the wrong engineer is not.
- Keep the confidence label. Dropping it turns a ranked guess into a false claim.
- Quote the engine's reasons verbatim; they are the audit trail.
- Never commit a drafted test without a human reviewing the assertion.
- Sanitise stack traces and logs — no PII, tokens, or customer identifiers — before putting them in a prompt.

## Adding a platform

Framework selection keys off the seed file's extension. Extend `_ANDROID_SUFFIXES` / `_IOS_SUFFIXES` in `tools/bugtrail/codesage.py` and add a branch in `tools/bugtrail/testgen.py`.

## Reference

- Design and data flow: [ARCHITECTURE.md](../../../tools/bugtrail/ARCHITECTURE.md)
- Setup, config, limitations: [README.md](../../../tools/bugtrail/README.md)
