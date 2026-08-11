#!/usr/bin/env python3
"""Watch Jira for new bugs and comment on them, with nothing else running.

Runs from cron, a server, or a scheduled CI job. Polling is used rather than a
webhook on purpose: a webhook needs a publicly reachable endpoint and a Jira
admin to configure it, while a poller needs only an API token the reporter can
issue themselves. It is also naturally idempotent - a missed tick is picked up
on the next one instead of being lost.

Credentials come from the environment, never from a file in the repository:

    export JIRA_BASE_URL=https://your-site.atlassian.net
    export JIRA_EMAIL=you@example.com
    export JIRA_API_TOKEN=...            # id.atlassian.com -> API tokens

Scope is opt-in by label. A bug under any story, in any project, gets picked up
the moment someone adds the label, and drops out again when they remove it. No
redeploy, no admin, and nobody is opted in without asking.

Shadow mode is the default. Posting requires --post, so a misconfigured run
cannot write to a real ticket.

    # see what it would say, touching nothing
    python3 tools/bugtrail/jira_agent.py --label bugtrail

    # actually comment, once
    python3 tools/bugtrail/jira_agent.py --label bugtrail --post --once

    # keep watching
    python3 tools/bugtrail/jira_agent.py --label bugtrail --post --watch

    # narrower scopes, if a team wants them
    ... --label bugtrail --project PLAY
    ... --parent PLAY-126471
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cli import analyse, jira_source, load_config  # noqa: E402
from jira_bot import MARKER, github_base, render_comment  # noqa: E402
from repo import ensure_repo, resolve_ref  # noqa: E402


class JiraError(RuntimeError):
    pass


class Jira:
    """The smallest Jira client that does the job, on the standard library.

    Uses REST v2 because its comment bodies are wiki markup strings. v3 would
    require building Atlassian Document Format trees for every paragraph.
    """

    def __init__(self, base_url: str, email: str, token: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        raw = ("%s:%s" % (email, token)).encode()
        self.auth = "Basic " + base64.b64encode(raw).decode()

    def _call(self, method: str, path: str, payload=None, params=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self.auth)
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:400]
            raise JiraError("%s %s -> %s %s" % (method, path, e.code, detail))
        except urllib.error.URLError as e:
            raise JiraError("%s %s -> %s" % (method, path, e.reason))

    def search(self, jql: str, fields, max_results: int = 50):
        return self._call(
            "GET", "/rest/api/2/search",
            params={"jql": jql, "fields": ",".join(fields), "maxResults": max_results},
        ).get("issues", [])

    def comments(self, key: str):
        return self._call("GET", "/rest/api/2/issue/%s/comment" % key).get("comments", [])

    def add_comment(self, key: str, body: str):
        return self._call("POST", "/rest/api/2/issue/%s/comment" % key, {"body": body})

    def update_comment(self, key: str, comment_id: str, body: str):
        return self._call(
            "PUT", "/rest/api/2/issue/%s/comment/%s" % (key, comment_id), {"body": body}
        )

    def whoami(self):
        return self._call("GET", "/rest/api/2/myself")


def existing_comment_id(jira: Jira, key: str):
    """Our own previous comment, so a re-run updates rather than duplicates."""
    for c in jira.comments(key):
        if MARKER in (c.get("body") or ""):
            return c.get("id")
    return None


def build_jql(
    parent: str = "",
    label: str = "",
    project: str = "",
    extra_jql: str = "",
    issue_types=("Bug",),
    since_days: int = 0,
) -> str:
    """Scope the search.

    A label is the preferred scope. Anyone who wants attribution on their bug
    adds the label, from any story in any project, and nobody has to redeploy
    the agent to widen a parent. It is also the only scope a reporter can grant
    and revoke themselves.

    Types are constrained because attribution only makes sense for defects: a
    Story or a Task describes work to do, so no change ever "caused" it.
    """
    clauses = []
    if label:
        clauses.append("labels = %s" % label)
    if parent:
        clauses.append(
            '(parent = %s OR issue in linkedIssues("%s"))' % (parent, parent)
        )
    if project:
        clauses.append("project = %s" % project)
    if extra_jql:
        clauses.append("(%s)" % extra_jql)

    types = [t.strip() for t in issue_types if t and t.strip()]
    if types:
        clauses.append("issuetype in (%s)" % ", ".join('"%s"' % t for t in types))

    # Without a floor, a label typo that matches an old bug drags the whole
    # back catalogue into scope on the first run.
    if since_days:
        clauses.append("created >= -%dd" % since_days)

    return " AND ".join(clauses) + " ORDER BY created DESC"


def find_bugs(jira: Jira, jql: str):
    print("[jql] %s" % jql)
    return jira.search(
        jql, ["summary", "description", "created", "issuetype", "labels"]
    )


def process(jira: Jira, issue, repo: str, config: dict, base: str, args) -> str:
    key = issue["key"]
    fields = issue.get("fields") or {}

    # Checked again here rather than trusting the JQL alone: --jql is
    # caller-supplied, and commenting on the wrong issue type is not recoverable.
    allowed = [t.strip().lower() for t in args.issue_types.split(",") if t.strip()]
    kind = ((fields.get("issuetype") or {}).get("name") or "").lower()
    if allowed and kind not in allowed:
        return "skipped: issue type %r not in %s" % (kind, allowed)

    comment_id = existing_comment_id(jira, key)
    if comment_id and not args.update:
        return "already commented"

    scratch = Path(args.state_dir) / "_issue.json"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(json.dumps({
        "key": key,
        "summary": fields.get("summary") or "",
        "description": fields.get("description") or "",
        "created": fields.get("created"),
    }))

    bug, ranked = jira_source(scratch, repo, config)
    if bug is None:
        return "no candidate file located"

    result = analyse(repo, bug, config)
    if not result.suspects or result.confidence < args.min_confidence:
        return "confidence %.2f below %.2f" % (result.confidence, args.min_confidence)

    body = render_comment(result, ranked, repo, base)

    if not args.post:
        print("\n----- would post to %s -----\n%s\n" % (key, body))
        return "shadow mode (conf %.2f)" % result.confidence

    if comment_id:
        jira.update_comment(key, comment_id, body)
        return "updated comment (conf %.2f)" % result.confidence
    jira.add_comment(key, body)
    return "commented (conf %.2f)" % result.confidence


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--label",
        default="",
        help="act on bugs carrying this label, wherever they live. The opt-in "
        "scope: reporters add the label when they want attribution",
    )
    ap.add_argument("--parent", help="only act on children or links of this issue")
    ap.add_argument("--project", default="", help="restrict to one project key")
    ap.add_argument("--jql", default="", help="extra JQL, ANDed with the rest")
    ap.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="ignore bugs created longer ago than this. 0 disables the floor",
    )
    ap.add_argument(
        "--issue-types",
        default="Bug",
        help="comma-separated issue types to act on. Defaults to Bug; "
        "attribution is meaningless for a Story or a Task",
    )
    ap.add_argument("--repo")
    ap.add_argument("--ref")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--post", action="store_true", help="actually comment; off by default")
    ap.add_argument("--update", action="store_true", help="refresh an existing comment")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=120)
    ap.add_argument("--min-confidence", type=float, default=0.25)
    ap.add_argument("--history-limit", type=int, default=60)
    ap.add_argument("--state-dir", default=str(Path.home() / ".cache" / "bugtrail"))
    args = ap.parse_args()

    if not (args.label or args.parent or args.jql):
        print(
            "refusing to run unscoped: pass --label, --parent or --jql",
            file=sys.stderr,
        )
        return 2

    missing = [v for v in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
               if not os.environ.get(v)]
    if missing:
        print("missing environment: %s" % ", ".join(missing), file=sys.stderr)
        print(__doc__.split("Credentials")[1].split("Shadow")[0], file=sys.stderr)
        return 2

    jira = Jira(
        os.environ["JIRA_BASE_URL"],
        os.environ["JIRA_EMAIL"],
        os.environ["JIRA_API_TOKEN"],
    )
    me = jira.whoami()
    print("[jira] authenticated as %s" % me.get("displayName", me.get("emailAddress")))

    config = load_config(Path(args.config))
    config["historyLimit"] = args.history_limit
    repo = ensure_repo(args.repo or config.get("repo") or "", fetch_local=True)
    config["ref"] = resolve_ref(repo, args.ref)
    base = github_base(repo)
    print("[repo] %s at %s" % (repo, config["ref"]))
    print("[mode] %s" % ("POSTING" if args.post else "shadow (nothing will be written)"))

    jql = build_jql(
        parent=args.parent or "",
        label=args.label,
        project=args.project,
        extra_jql=args.jql,
        issue_types=args.issue_types.split(","),
        since_days=args.since_days,
    )

    while True:
        try:
            issues = find_bugs(jira, jql)
            print("\n[%s] %d issue(s) in scope"
                  % (time.strftime("%H:%M:%S"), len(issues)))
            for issue in issues:
                try:
                    outcome = process(jira, issue, repo, config, base, args)
                except JiraError as e:
                    outcome = "jira error: %s" % e
                print("  %-14s %s" % (issue["key"], outcome))
        except JiraError as e:
            print("  search failed: %s" % e, file=sys.stderr)

        if args.once or not args.watch:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
