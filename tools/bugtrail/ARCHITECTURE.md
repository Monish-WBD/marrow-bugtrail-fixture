# BugTrail — architecture and design summary

## Guiding decision

**Keep the model out of the load-bearing path.**

Everything that determines *which* pull request gets named is deterministic and reproducible from the repository alone: git history, rename tracking, commit-to-PR mapping, exclusion rules, and the scoring arithmetic. A model is only appropriate where judgement is genuinely required — writing a test's assertion, or phrasing a summary.

This is what makes the output defensible. Any suspect can be re-derived by hand with `git log`, and any score can be recomputed from the printed reasons. Nothing has to be taken on trust.

## Components

```
                      ┌──────────────────────────┐
                      │  CodeSage triage comment │   (file today, Jira later)
                      └────────────┬─────────────┘
                                   │
                       codesage.py │  ADF flatten + regex parse, fails closed
                                   ▼
                      ┌──────────────────────────┐
                      │   TriageInput (models)   │   normalised seed
                      └────────────┬─────────────┘
                                   │
              archaeology.py       │  git only, no model
              ┌────────────────────┴────────────────────┐
              │ history (--follow) + module siblings    │
              │ commit → PR  (squash | merge | none)    │
              │ exclusions: bot, revert, generated,     │
              │             post-report                 │
              │ diff analysis: substantive? rename?     │
              │              keyword hits, symbols      │
              └────────────────────┬────────────────────┘
                                   ▼
                      ┌──────────────────────────┐
                 ranking.py        │ four weighted signals   │
                      │  + cosmetic/rename penalty         │
                      │  + one entry per PR                │
                      │  + confidence threshold            │
                      └────────────┬─────────────┘
                                   ▼
      codeowners.py ──────────► report.py ◄────────── testgen.py
      owning team, and            full report          framework-correct
      agreement with              (console today,      scaffold, assertion
      CodeSage's team             Jira comment later)  left to a human
```

## Ports

The engine never learns where its input came from or where its output goes.

| Port | Today | Later |
| --- | --- | --- |
| Triage source | `codesage_source` reads a comment file; `fixture_source` reads the manifest | `JiraSource` via the approved Atlassian integration |
| Result sink | console renderer | idempotent Jira comment |

Adding Jira is therefore a new adapter, not a change to archaeology or ranking. It also means the whole pipeline is testable with no network.

## Data flow

1. **Parse.** The CodeSage comment yields the seed file, summary, priority, severity, component, and suggested team. Missing seed file ⇒ return `None` and stop; a wrong seed produces confidently wrong attribution, which is worse than no answer.
2. **Route.** Platform is inferred from the seed file's extension (`.kt`/`.java` ⇒ Android, `.swift`/`.m` ⇒ iOS). This is what lets one implementation serve both repositories.
3. **Widen.** CodeSage supplies a *starting point*, not a verdict, so the candidate set is the seed file plus its directory siblings. The real cause is often next door.
4. **Walk.** For each path, `git log --follow` recovers history across renames. Crucially, each commit is analysed against **its own historical path** — see [Bugs found](#bugs-found-during-development).
5. **Attribute.** Squash merges carry `(#N)` in the subject; merge commits carry `Merge pull request #N`. A commit merged *via* a merge commit carries neither, so the earliest containing merge is found by walking `--ancestry-path` forward to `HEAD`.
6. **Exclude.** Bots, reverts, generated sources, and commits that landed after the report are dropped, each with a printed reason.
7. **Score.** Four weighted signals, summing to 1.0, then penalties.
8. **Report.** Owner resolution, the suspect diff, and a drafted test.

## Scoring

| Signal | Weight | Rationale |
| --- | --- | --- |
| Recency | 0.35 | Exponential decay toward the report date. Regressions are usually recent. |
| Keyword overlap | 0.35 | Matched against **code** lines only, so comments cannot inflate it. |
| Seed-file match | 0.20 | The file CodeSage named outranks a module sibling. |
| Substantiveness | 0.10 | Did the change touch code at all? |

Then: comment-only and rename-only changes are multiplied by `1 - cosmeticPenalty`. This is the rule that does the real discriminating work — in the fixture, a docs-only commit lands on the culprit file *one day later* than the true cause, so pure recency gets it wrong.

Candidates are collapsed to one entry per pull request, keeping the highest scorer. If the top score falls below `minConfidence`, the report says so instead of naming anyone.

## Bugs found during development

Both are worth knowing, because both only appear on realistic repositories.

**Renames faked keyword matches.** Git renders a pure rename as a whole-file delete plus add, so the rename commit appeared to mention every keyword in the bug report and outranked the true culprit. Fixed by detecting renames with `--name-status -M` — run *without* a pathspec, since rename detection needs both sides of the diff — and treating them as non-behavioural.

**Historical paths broke diff analysis.** `--follow` correctly walked back past a rename to the true culprit, but the engine then diffed that old commit against the file's *current* name, which did not exist yet. The diff came back empty, the change looked cosmetic, and the real cause was penalised out of the results entirely. Fixed by parsing `--name-status` alongside the log to recover each commit's path *at that commit*.

The second is the more instructive failure: it is invisible on a repository where nothing has ever been moved, and guaranteed on one where things have.

## Files

| File | Lines | Responsibility |
| --- | --- | --- |
| `archaeology.py` | ~376 | git history, PR mapping, exclusions, diff analysis |
| `cli.py` | ~255 | argument handling, sources, orchestration |
| `report.py` | ~171 | full report rendering |
| `codesage.py` | ~140 | ADF flattening and comment parsing |
| `testgen.py` | ~137 | regression test scaffolding |
| `ranking.py` | ~123 | keyword extraction and scoring |
| `models.py` | ~82 | shared data types |
| `codeowners.py` | ~75 | owner resolution and team agreement |
| `tests/test_bugtrail.py` | ~215 | 15 tests |

## Model-agnostic by construction

There is no model in the pipeline today, and the two places one belongs — drafting a test assertion, phrasing a summary — sit behind a boundary the engine does not know about. Swapping model or vendor cannot change which PR is named, and `--eval` exists to prove that after any change.
