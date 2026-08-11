"""Tests for the deterministic parts of BugTrail.

The CodeSage sample is treated as a contract: if the upstream comment format
changes, these tests fail loudly rather than the parser silently returning None
and the tool quietly posting nothing.
"""

import argparse
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import jira_webhook  # noqa: E402
from codesage import flatten_adf, parse_adf_comment, parse_comment  # noqa: E402
from jira_agent import build_jql, is_scope_narrow_enough  # noqa: E402
from models import Candidate, Commit  # noqa: E402
from ranking import extract_keywords, rank  # noqa: E402

FIXTURES = HERE.parents[2] / "fixtures" / "triage"

CONFIG = {
    "halfLifeDays": 30,
    "moduleWeight": 0.5,
    "cosmeticPenalty": 0.75,
    "minConfidence": 0.0,
    "maxSuspects": 5,
    "weights": {
        "recency": 0.35,
        "keyword": 0.35,
        "seedFile": 0.20,
        "substantive": 0.10,
    },
}

REPORTED_AT = datetime(2026, 7, 2, tzinfo=timezone.utc)


def make_candidate(
    sha="abc1234",
    subject="fix: something",
    pr_number=1,
    days_before=1,
    is_seed_file=True,
    is_substantive=True,
    is_rename=False,
    keyword_hits=("marker",),
):
    commit = Commit(
        sha=sha,
        author_name="Test Author",
        author_email="test@example.com",
        authored_at=REPORTED_AT - timedelta(days=days_before),
        subject=subject,
        parents=("parent1",),
    )
    return Candidate(
        commit=commit,
        path="Some/Path.swift",
        is_seed_file=is_seed_file,
        pr_number=pr_number,
        lines_changed=4,
        is_substantive=is_substantive,
        keyword_hits=keyword_hits,
        is_rename=is_rename,
    )


class TestCodeSageParser(unittest.TestCase):
    def setUp(self):
        self.text = (FIXTURES / "SYN-001.txt").read_text()

    def test_extracts_every_field(self):
        triage = parse_comment(self.text)
        self.assertIsNotNone(triage)
        self.assertEqual(
            triage.seed_file, "TASK/TASK/Modules/Player/AdSkipManager.swift"
        )
        self.assertEqual(triage.suggested_priority, "P1")
        self.assertEqual(triage.suggested_severity, "S2")
        self.assertEqual(triage.suggested_component, "iOS Player - Timeline")
        self.assertEqual(triage.suggested_team, "MARROW-iOS")
        self.assertIn("Skip Intro", triage.summary)
        self.assertTrue(triage.where_to_start)

    def test_summary_stops_before_thought_logic(self):
        triage = parse_comment(self.text)
        self.assertNotIn("Thought Logic", triage.summary)
        self.assertNotIn("Priority:", triage.summary)

    def test_detects_regression_wording(self):
        self.assertTrue(parse_comment(self.text).is_regression)

    def test_platform_from_extension(self):
        ios = parse_comment((FIXTURES / "SYN-001.txt").read_text())
        android = parse_comment((FIXTURES / "SYN-002.txt").read_text())
        self.assertEqual(ios.platform, "ios")
        self.assertEqual(android.platform, "android")

    def test_returns_none_when_not_codesage(self):
        self.assertIsNone(parse_comment("Just an ordinary comment from a human."))

    def test_fails_closed_without_starting_point(self):
        stripped = "\n".join(
            ln for ln in self.text.splitlines() if not ln.strip().startswith("- File:")
        )
        self.assertIsNone(parse_comment(stripped))

    def test_fails_closed_on_empty_file_line(self):
        """Observed in production: CodeSage emits "- File:" with no value and
        describes the file in prose instead. The empty value must not swallow
        the following line and pass it off as a path."""
        comment = (
            "AI Triage Suggestion (CodeSage)\n"
            "Suggested Priority: P2\n"
            "Starting Point:\n"
            "- File: \n"
            "- Where to start: File search identified the highly relevant file "
            "`MediaSourceProvider.kt`, but the full path was not available.\n"
        )
        self.assertIsNone(parse_comment(comment))

    def test_empty_field_does_not_absorb_next_line(self):
        comment = (
            "AI Triage Suggestion (CodeSage)\n"
            "Suggested Team: \n"
            "Suggested Severity: S3\n"
            "- File: a/b/Thing.kt\n"
        )
        triage = parse_comment(comment)
        self.assertIsNotNone(triage)
        self.assertEqual(triage.seed_file, "a/b/Thing.kt")
        self.assertNotEqual(triage.suggested_team, "Suggested Severity: S3")
        self.assertEqual(triage.suggested_severity, "S3")


class TestAdfFlattening(unittest.TestCase):
    def test_flattens_paragraphs_into_lines(self):
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "AI Triage Suggestion"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Suggested Priority: P1"}],
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "- File: path/To/Thing.kt"}
                    ],
                },
            ],
        }
        text = flatten_adf(doc)
        self.assertIn("Suggested Priority: P1", text)
        self.assertIn("- File: path/To/Thing.kt", text)

    def test_parses_straight_from_adf(self):
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "AI Triage Suggestion (CodeSage)"}
                    ],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "- File: a/b/Thing.kt"}],
                },
            ],
        }
        triage = parse_adf_comment(json.dumps(doc))
        self.assertIsNotNone(triage)
        self.assertEqual(triage.seed_file, "a/b/Thing.kt")
        self.assertEqual(triage.platform, "android")


class TestRanking(unittest.TestCase):
    def _score_of(self, candidate):
        suspects, _ = rank([candidate], REPORTED_AT, ("marker",), CONFIG)
        return suspects[0].score

    def test_comment_only_change_is_penalised(self):
        substantive = self._score_of(make_candidate(is_substantive=True))
        cosmetic = self._score_of(
            make_candidate(is_substantive=False, keyword_hits=())
        )
        self.assertLess(cosmetic, substantive)

    def test_rename_is_penalised(self):
        real = self._score_of(make_candidate())
        renamed = self._score_of(
            make_candidate(is_rename=True, is_substantive=False, keyword_hits=())
        )
        self.assertLess(renamed, real)

    def test_recent_change_outranks_old_one(self):
        recent = self._score_of(make_candidate(days_before=1))
        old = self._score_of(make_candidate(days_before=200))
        self.assertGreater(recent, old)

    def test_seed_file_outranks_module_sibling(self):
        seed = self._score_of(make_candidate(is_seed_file=True))
        sibling = self._score_of(make_candidate(is_seed_file=False))
        self.assertGreater(seed, sibling)

    def test_one_entry_per_pull_request(self):
        candidates = [
            make_candidate(sha="aaa", pr_number=7, days_before=5),
            make_candidate(sha="bbb", pr_number=7, days_before=1),
        ]
        suspects, _ = rank(candidates, REPORTED_AT, ("marker",), CONFIG)
        self.assertEqual(len(suspects), 1)
        self.assertEqual(suspects[0].candidate.commit.sha, "bbb")

    def test_below_threshold_yields_no_suspects(self):
        config = dict(CONFIG, minConfidence=0.99)
        suspects, confidence = rank(
            [make_candidate()], REPORTED_AT, ("marker",), config
        )
        self.assertEqual(suspects, [])
        self.assertGreater(confidence, 0)


class TestKeywords(unittest.TestCase):
    def test_drops_stopwords_and_short_tokens(self):
        keywords = extract_keywords("The Skip Intro button is missing on the ad tier")
        self.assertIn("skip", keywords)
        self.assertIn("intro", keywords)
        self.assertNotIn("the", keywords)
        self.assertNotIn("button", keywords)


class TestScope(unittest.TestCase):
    """The JQL is the safety boundary. Everything downstream trusts it, so a
    silent widening here is how the agent ends up commenting on real tickets
    nobody asked it to touch.
    """

    def test_label_scope_needs_no_parent(self):
        jql = build_jql(label="bugtrail")
        self.assertIn("labels = bugtrail", jql)
        self.assertNotIn("parent", jql)

    def test_always_constrains_issue_type(self):
        for jql in (build_jql(label="x"), build_jql(parent="P-1"),
                    build_jql(extra_jql="reporter = me")):
            self.assertIn('issuetype in ("Bug")', jql)

    def test_extra_jql_cannot_widen_scope(self):
        jql = build_jql(label="bugtrail", extra_jql="status = New OR status = Open")
        self.assertIn("(status = New OR status = Open)", jql)
        self.assertTrue(jql.startswith("labels = bugtrail AND ("))

    def test_bug_only_needs_no_narrowing(self):
        self.assertTrue(is_scope_narrow_enough("Bug", "", ""))

    def test_subtask_requires_a_story_or_label(self):
        self.assertFalse(is_scope_narrow_enough("Bug,Sub-task", "", ""))
        self.assertTrue(is_scope_narrow_enough("Bug,Sub-task", "", "PLAY-126471"))
        self.assertTrue(is_scope_narrow_enough("Bug,Sub-task", "bugtrail", ""))

    def test_age_floor_is_applied(self):
        self.assertIn("created >= -7d", build_jql(label="x", since_days=7))
        self.assertNotIn("created >=", build_jql(label="x", since_days=0))


class TestWebhook(unittest.TestCase):
    """The webhook is reachable from the internet, so its refusals matter more
    than its successes.
    """

    @classmethod
    def setUpClass(cls):
        cls.seen = []

        class FakeJira:
            def search(inner, jql, fields, max_results=50):
                cls.seen.append(jql)
                if "OUT-1" in jql:
                    return []
                return [{
                    "key": "IN-1",
                    "fields": {
                        "summary": "s", "description": "d",
                        "created": "2026-08-11T09:48:10.963+0000",
                        "issuetype": {"name": "Sub-task"},
                    },
                }]

        cls._real_process = jira_webhook.process
        jira_webhook.process = lambda *a, **k: "processed"

        args = argparse.Namespace(
            parent="PLAY-126471", label="", issue_types="Sub-task",
            update=False, min_confidence=0.25, post=False,
            state_dir=tempfile.mkdtemp(),
        )
        jira_webhook.Handler.context = {
            "jira": FakeJira(), "repo": ".", "config": {}, "base": "",
            "args": args, "secret": "s3cret",
        }
        # Port 0: the OS picks a free one, so parallel runs cannot collide.
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), jira_webhook.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        jira_webhook.process = cls._real_process

    def post(self, body, token="s3cret", path="/jira"):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(body).encode(), method="POST",
        )
        if token is not None:
            req.add_header("X-BugTrail-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_rejects_wrong_and_missing_token(self):
        self.assertEqual(self.post({"key": "IN-1"}, token="wrong"), 401)
        self.assertEqual(self.post({"key": "IN-1"}, token=None), 401)

    def test_rejects_unknown_path_and_empty_key(self):
        self.assertEqual(self.post({"key": "IN-1"}, path="/elsewhere"), 404)
        self.assertEqual(self.post({}), 400)

    def test_accepts_a_scoped_issue(self):
        self.assertEqual(self.post({"key": "IN-1"}), 202)

    def test_holding_the_secret_does_not_widen_scope(self):
        # Answered 202 because the work is asynchronous; the scope check then
        # declines it, which is visible in the JQL rather than the status code.
        self.assertEqual(self.post({"key": "OUT-1"}), 202)
        time.sleep(0.3)
        self.assertTrue(any("key = OUT-1" in j for j in self.seen))


if __name__ == "__main__":
    unittest.main(verbosity=2)
