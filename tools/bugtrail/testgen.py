"""Draft a failing regression test for the top suspect.

Scope note, because this is the part people over-claim: the scaffold here is
deterministic. It derives the test's location, framework, class name, and the
symbol under test from the suspect diff. It deliberately does NOT invent the
assertion - that requires understanding intended behaviour, which is exactly the
part a model (or the engineer) should fill in, and exactly the part nobody should
accept unreviewed.

So the output is a compilable-shaped starting point with the suspect change
quoted inline, not a finished test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional


@dataclass
class DraftTest:
    path: str
    framework: str
    content: str


def _type_name(seed_file: str) -> str:
    return PurePosixPath(seed_file).stem


def _kotlin_package(seed_file: str) -> Optional[str]:
    parts = PurePosixPath(seed_file).parts
    for marker in ("java", "kotlin"):
        if marker in parts:
            idx = parts.index(marker)
            pkg = parts[idx + 1 : -1]
            if pkg:
                return ".".join(pkg)
    return None


def _quote(hunk: list, prefix: str) -> str:
    if not hunk:
        return "%s (no diff available)" % prefix
    return "\n".join("%s %s" % (prefix, line) for line in hunk)


def draft(
    platform: Optional[str],
    seed_file: str,
    bug_id: str,
    suspect_label: str,
    suspect_subject: str,
    symbols: list,
    hunk: list,
) -> Optional[DraftTest]:
    type_name = _type_name(seed_file)
    symbol = symbols[0] if symbols else "behaviourUnderTest"

    if platform == "ios":
        path = "TASK/TASKTests/Player/%sRegressionTests.swift" % type_name
        content = """import XCTest

/// Regression test drafted by Bug Slayers Bot for {bug_id}.
/// Suspected cause: {suspect_label} - {suspect_subject}
///
/// Suspect change:
{quoted_hunk}
///
/// This test should FAIL against the suspect commit and PASS once fixed.
final class {type_name}RegressionTests: XCTestCase {{

    func test_{symbol}_behavesAsExpected() throws {{
        // Arrange: build the state described in the bug report.
        // TODO(bug-slayers): construct {type_name} with the inputs from the report.

        // Act: exercise {symbol}, the symbol changed by {suspect_label}.

        // Assert: encode the behaviour the bug says was lost.
        XCTFail("Regression test not implemented yet - see suspect change above")
    }}
}}
""".format(
            bug_id=bug_id,
            suspect_label=suspect_label,
            suspect_subject=suspect_subject,
            quoted_hunk=_quote(hunk, "///"),
            type_name=type_name,
            symbol=symbol,
        )
        return DraftTest(path=path, framework="XCTest", content=content)

    if platform == "android":
        package = _kotlin_package(seed_file)
        src_dir = str(PurePosixPath(seed_file).parent).replace(
            "src/main/java", "src/test/java"
        )
        path = "%s/%sRegressionTest.kt" % (src_dir, type_name)
        header = "package %s\n\n" % package if package else ""
        content = """{header}import org.junit.Assert.fail
import org.junit.Test

/**
 * Regression test drafted by Bug Slayers Bot for {bug_id}.
 * Suspected cause: {suspect_label} - {suspect_subject}
 *
 * Suspect change:
{quoted_hunk}
 *
 * This test should FAIL against the suspect commit and PASS once fixed.
 */
class {type_name}RegressionTest {{

    @Test
    fun `{symbol} behaves as expected`() {{
        // Arrange: build the state described in the bug report.
        // TODO(bug-slayers): construct {type_name} with the inputs from the report.

        // Act: exercise {symbol}, the symbol changed by {suspect_label}.

        // Assert: encode the behaviour the bug says was lost.
        fail("Regression test not implemented yet - see suspect change above")
    }}
}}
""".format(
            header=header,
            bug_id=bug_id,
            suspect_label=suspect_label,
            suspect_subject=suspect_subject,
            quoted_hunk=_quote(hunk, " *"),
            type_name=type_name,
            symbol=symbol,
        )
        return DraftTest(path=path, framework="JUnit", content=content)

    return None
