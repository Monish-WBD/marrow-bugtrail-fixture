# Request: Jira service account for Bug Slayers Bot

Draft for an IT / Jira administration ticket. The precedent argument is the
important part — CodeSage already has exactly this, in the same project, so this
is a repeat of an approved pattern rather than a new one.

---

**Summary:** Create a Jira service account for automated bug-triage comments (Bug Slayers Bot)

**Type:** Access request · **Project affected:** PLAY

### What I need

A Jira Cloud service account, display name **Bug Slayers Bot**, following the
existing naming convention — suggested id `svc-wbdstreaming-play-bugslayers` —
with an API token I can store as a secret.

### Why

Bug Slayers Bot is a triage tool built for the PSDK AI Hackathon. When a bug is
filed it identifies the pull request that most likely caused the regression,
along with the author and the starting-point file, and posts that as a comment.

It currently authenticates with my personal API token, so every comment it
writes is attributed to **Monish K**. That is misleading in three ways that
matter:

1. Readers cannot tell an automated suggestion from a colleague's judgement,
   which is precisely the distinction triage comments need to make.
2. The audit trail records a person taking actions a program took.
3. The token carries my full user permissions, when the tool needs two.

### Precedent

CodeSage posts to this same project as `svc-wbdstreaming-play-codesage`
(account type `app`), and *Automation for Jira* posts as an app account. Both
appear on PLAY-124001 today. This request asks for the same arrangement.

### Permissions needed

Least privilege — the tool reads bugs and writes one comment per bug:

| Permission | Scope | Why |
| --- | --- | --- |
| Browse projects | PLAY | Read the bug summary and description |
| Add comments | PLAY | Post the triage comment |

It does **not** need to edit or transition work items, change fields, assign,
delete comments, or administer the project. If comment editing is separable, it
also updates its own previous comment rather than posting duplicates, so
*Edit own comments* would be useful; if that is bundled with broader edit
rights, it can be dropped and the tool will post once per issue instead.

### Credential handling

The API token would live in GitHub repository secrets, not in a repository or on
a laptop. Happy to follow whatever rotation schedule you set, and to name an
owner for it.

### Impact if declined

The tool still works; its comments continue to be attributed to a person rather
than a bot. That is a legibility and audit problem rather than a functional one,
so this is not urgent — but it is the difference between a demo and something
the team can trust in the backlog.
