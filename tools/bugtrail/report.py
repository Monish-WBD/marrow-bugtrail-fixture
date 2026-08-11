"""Render the full attribution report.

Shaped like the Jira comment this will eventually post, so the console output and
the posted comment stay the same artefact. Wording is deliberately non-accusatory:
"likely related changes", never "who broke it".
"""

from __future__ import annotations

from archaeology import changed_symbols, diff_hunk, merge_strategy
from codeowners import matches_suggested_team, owners_for
from models import Result
from ranking import confidence_label
from testgen import draft

WIDTH = 80


def _rule(char: str = "-") -> str:
    return char * WIDTH


def _wrap(text: str, indent: str = "  ", width: int = WIDTH - 2) -> list:
    words = " ".join(text.split()).split(" ")
    lines, current = [], indent
    for word in words:
        if len(current) + len(word) + 1 > width and current.strip():
            lines.append(current.rstrip())
            current = indent + word + " "
        else:
            current += word + " "
    if current.strip():
        lines.append(current.rstrip())
    return lines


def render_report(repo: str, result: Result) -> str:
    bug = result.bug
    triage = bug.triage
    out = []

    out.append(_rule("="))
    out.append("BUG %s" % bug.bug_id)
    if bug.display_title:
        out.append(bug.display_title)
    out.append(_rule("="))
    out.append("")

    out.append("Reported        %s UTC" % bug.reported_at.strftime("%Y-%m-%d %H:%M"))
    out.append("Platform        %s" % (bug.platform or "unknown"))
    if triage is not None:
        out.append(
            "CodeSage        priority %s | severity %s | component %s"
            % (
                triage.suggested_priority or "-",
                triage.suggested_severity or "-",
                triage.suggested_component or "-",
            )
        )
        out.append("Suggested team  %s" % (triage.suggested_team or "-"))
        out.append(
            "Regression      %s" % ("yes (per CodeSage summary)" if triage.is_regression else "not stated")
        )
    out.append("")

    if triage is not None and triage.summary:
        out.append("DESCRIPTION (from CodeSage triage)")
        out.extend(_wrap(triage.summary))
        out.append("")

    owners = owners_for(repo, bug.seed_file)
    agreement = matches_suggested_team(
        owners, triage.suggested_team if triage is not None else None
    )
    out.append("SEED FILE (CodeSage starting point)")
    out.append("  %s" % bug.seed_file)
    if owners:
        suffix = ""
        if agreement is True:
            suffix = "   (agrees with CodeSage's suggested team)"
        elif agreement is False:
            suffix = "   (does NOT match CodeSage's suggested team)"
        out.append("  CODEOWNERS: %s%s" % (" ".join(owners), suffix))
    else:
        out.append("  CODEOWNERS: no rule matched this path")
    if triage is not None and triage.where_to_start:
        out.append("")
        out.append("  Where to start (CodeSage):")
        out.extend(_wrap(triage.where_to_start, indent="    "))
    out.append("")

    out.append(_rule())
    out.append(
        "LIKELY RELATED CHANGES%sconfidence: %s (%.2f)"
        % (
            " " * (WIDTH - 22 - len("confidence: X (0.00)") - 4),
            confidence_label(result.confidence),
            result.confidence,
        )
    )
    out.append(_rule())
    out.append("")

    if not result.suspects:
        out.append("  No change scored above the confidence threshold.")
        out.append("  Leaving this one for a human rather than guessing.")
        out.append("")
    else:
        for i, suspect in enumerate(result.suspects, 1):
            c = suspect.candidate
            commit = c.commit
            out.append('%d.  %s   "%s"' % (i, suspect.label, commit.subject))
            out.append("    commit    %s  (%s)" % (commit.short_sha, merge_strategy(commit)))
            out.append("    author    %s <%s>" % (commit.author_name, commit.author_email))
            out.append("    landed    %s" % commit.authored_at.strftime("%Y-%m-%d %H:%M"))
            out.append("    file      %s" % c.path)
            out.append("    score     %.2f   (%d line(s) changed)" % (suspect.score, c.lines_changed))
            out.append("    why")
            for reason in suspect.reasons:
                out.append("              - %s" % reason)

            if i == 1:
                hunk = diff_hunk(repo, commit.sha, c.path)
                if hunk:
                    out.append("")
                    out.append("    suspect change")
                    for line in hunk:
                        out.append("      %s" % line)
            out.append("")

    if result.excluded:
        out.append(_rule())
        out.append("EXCLUDED FROM CONSIDERATION")
        out.append(_rule())
        for note in dict.fromkeys(result.excluded):
            out.append("  - %s" % note)
        out.append("")

    if result.suspects:
        top = result.suspects[0]
        symbols = changed_symbols(repo, top.candidate.commit.sha, top.candidate.path)
        test = draft(
            platform=bug.platform,
            seed_file=bug.seed_file,
            bug_id=bug.bug_id,
            suspect_label=top.label,
            suspect_subject=top.candidate.commit.subject,
            symbols=symbols,
            hunk=diff_hunk(repo, top.candidate.commit.sha, top.candidate.path, max_lines=6),
        )
        out.append(_rule())
        out.append("PROPOSED REGRESSION TEST  (draft, not committed)")
        out.append(_rule())
        if test is None:
            out.append("  Unknown platform, so no framework could be selected.")
        else:
            out.append("  %s  [%s]" % (test.path, test.framework))
            if symbols:
                out.append("  symbol under test: %s" % ", ".join(symbols))
            out.append("")
            for line in test.content.splitlines():
                out.append("  | %s" % line)
        out.append("")

    out.append(_rule("="))
    out.append(
        "Suspect changes are ranked suggestions, not attributions of fault."
    )
    out.append("Verify before assigning. Generated by Bug Slayers Bot.")
    out.append(_rule("="))
    return "\n".join(out)
