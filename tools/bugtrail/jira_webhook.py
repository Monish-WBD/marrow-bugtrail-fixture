#!/usr/bin/env python3
"""Comment the moment a bug is filed, instead of waiting for the next poll.

Polling is simpler and needs no permissions, but it is only ever as fast as its
interval. This listens for a Jira Automation rule firing a *Send web request* on
issue creation, so the comment lands seconds after the reporter clicks Create.

The rule sends nothing but the issue key. Everything else - summary, type,
parent - is read back from Jira with our own credentials, because a request
arriving over the internet is not evidence about what a ticket contains.

Setup:

    1. Credentials, as for the poller:
           export JIRA_BASE_URL=... JIRA_EMAIL=... JIRA_API_TOKEN=...
           export BUGTRAIL_WEBHOOK_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")

    2. Run it, and expose it. Jira has to reach the port from the internet:
           python3 tools/bugtrail/jira_webhook.py --parent PLAY-126471 \
               --issue-types "Sub-task" --post
           cloudflared tunnel --url http://localhost:8787     # or ngrok http 8787

    3. In Jira: Project settings -> Automation -> Create rule
           When:  Work item created
           If:    Issue Type equals Sub-task   (and parent = your story)
           Then:  Send web request
                  URL     https://<your-tunnel>/jira
                  Method  POST
                  Headers X-BugTrail-Token: <the secret above>
                  Body    custom data:  {"key": "{{issue.key}}"}

Shadow mode is still the default; --post is required before anything is written.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cli import load_config  # noqa: E402
from jira_agent import (  # noqa: E402
    Jira,
    JiraError,
    build_jql,
    find_bugs,
    is_scope_narrow_enough,
    process,
)
from jira_bot import github_base  # noqa: E402
from repo import ensure_repo, resolve_ref  # noqa: E402

MAX_BODY = 64 * 1024


class Handler(BaseHTTPRequestHandler):
    # Injected by main; the http.server API gives no clean way to pass state.
    context = {}

    def _reply(self, code: int, message: str):
        payload = json.dumps({"status": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        if self.path.rstrip("/") != "/jira":
            return self._reply(404, "not found")

        secret = self.context["secret"]
        sent = self.headers.get("X-BugTrail-Token", "")
        # Constant-time: a plain == leaks the prefix length through timing.
        if not hmac.compare_digest(sent, secret):
            return self._reply(401, "bad token")

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._reply(400, "bad length")
        if length <= 0 or length > MAX_BODY:
            return self._reply(400, "bad length")

        try:
            payload = json.loads(self.rfile.read(length).decode())
            key = (payload or {}).get("key") or ""
        except Exception:
            return self._reply(400, "bad json")

        if not key:
            return self._reply(400, "no issue key")

        # Answer immediately and work afterwards. Jira times the request out
        # after a few seconds, and a retry storm would mean duplicate analysis.
        self._reply(202, "accepted %s" % key)
        threading.Thread(target=self._handle, args=(key,), daemon=True).start()

    def _handle(self, key: str):
        ctx = self.context
        args = ctx["args"]
        try:
            # Re-ask Jira for this issue *through the same scope the poller
            # uses*. Holding the secret proves the caller is our rule, not that
            # the ticket is one we agreed to comment on - a rule edited by
            # someone else would otherwise widen us silently.
            jql = build_jql(
                parent=args.parent,
                label=args.label,
                extra_jql="key = %s" % key,
                issue_types=args.issue_types.split(","),
            )
            issues = find_bugs(ctx["jira"], jql)
            if not issues:
                outcome = "out of scope"
            else:
                outcome = process(
                    ctx["jira"], issues[0], ctx["repo"], ctx["config"],
                    ctx["base"], args,
                )
        except JiraError as e:
            outcome = "jira error: %s" % e
        except Exception as e:  # a webhook thread dying silently is worse
            outcome = "failed: %s" % e
        print("  %-14s %s" % (key, outcome), flush=True)

    def log_message(self, fmt, *args):
        print("[http] " + fmt % args, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--parent", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--issue-types", default="Bug")
    ap.add_argument("--repo")
    ap.add_argument("--ref")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--min-confidence", type=float, default=0.25)
    ap.add_argument("--history-limit", type=int, default=60)
    ap.add_argument("--state-dir", default=str(Path.home() / ".cache" / "bugtrail"))
    args = ap.parse_args()

    if not is_scope_narrow_enough(args.issue_types, args.label, args.parent):
        print(
            "refusing to accept types beyond Bug without --parent or --label: %s"
            % args.issue_types,
            file=sys.stderr,
        )
        return 2

    needed = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "BUGTRAIL_WEBHOOK_SECRET")
    missing = [v for v in needed if not os.environ.get(v)]
    if missing:
        print("missing environment: %s" % ", ".join(missing), file=sys.stderr)
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

    Handler.context = {
        "jira": jira,
        "repo": repo,
        "config": config,
        "base": github_base(repo),
        "args": args,
        "secret": os.environ["BUGTRAIL_WEBHOOK_SECRET"],
    }

    print("[repo] %s at %s" % (repo, config["ref"]))
    print("[mode] %s" % ("POSTING" if args.post else "shadow (nothing written)"))
    print("[http] listening on http://%s:%d/jira" % (args.host, args.port))

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
