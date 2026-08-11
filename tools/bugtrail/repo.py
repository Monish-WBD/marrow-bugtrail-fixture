"""Make the analysed repository self-provisioning.

The reporter files a bug from anywhere; the agent runs somewhere fixed. That
somewhere should not need a human to have cloned anything first, and it must not
analyse a checkout that is days out of date - a stale clone cannot see the pull
request that caused yesterday's bug, so it silently blames something older.

Reading history over the GitHub API was considered and rejected: following a file
across renames and diffing each commit costs hundreds of calls per bug, and code
search over a repository this size is rate-limited and returns partial results.
A local mirror keeps the engine fast, complete, and reproducible by hand.

Blobs are fetched lazily (--filter=blob:none), so the first clone stays cheap and
file contents arrive only for the handful of commits actually inspected.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Optional

CACHE_DIR = Path.home() / ".cache" / "bugtrail" / "repos"
DEFAULT_MAX_AGE_SECONDS = 900


def is_remote(spec: str) -> bool:
    return bool(re.match(r"^(https?://|git@|ssh://|[\w.-]+/[\w.-]+$)", spec)) and not Path(spec).exists()


def _normalise(spec: str) -> str:
    if re.match(r"^[\w.-]+/[\w.-]+$", spec):
        return "https://github.com/%s.git" % spec
    return spec


def _name_of(url: str) -> str:
    m = re.search(r"[:/]([\w.-]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else "repo"


def _run(args, cwd: Optional[Path] = None, timeout: int = 900):
    return subprocess.run(
        args, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, timeout=timeout,
    )


def _fetch_marker(path: Path) -> Path:
    return path / ".git" / "bugtrail-last-fetch"


def ensure_repo(
    spec: str,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    quiet: bool = False,
    fetch_local: bool = False,
) -> str:
    """Return a local path for `spec`, cloning or refreshing as needed.

    `spec` may be a local path, a clone URL, or "owner/name". A path the caller
    supplied by hand is left alone unless `fetch_local` is set, so offline and
    fixture runs stay hermetic.
    """
    def say(msg: str) -> None:
        if not quiet:
            print("[repo] %s" % msg)

    local = Path(spec).expanduser()
    if local.exists() and (local / ".git").exists():
        path = local
        if not fetch_local:
            return str(path)
    elif not is_remote(spec):
        raise ValueError("%s is not a git repository and not a clone URL" % spec)
    else:
        url = _normalise(spec)
        path = CACHE_DIR / _name_of(url)
        if not (path / ".git").exists():
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            say("cloning %s (blobless) ..." % url)
            r = _run(["git", "clone", "--filter=blob:none", url, str(path)], timeout=3600)
            if r.returncode != 0:
                raise RuntimeError("clone failed: %s" % r.stderr.strip()[:400])
            _fetch_marker(path).write_text(str(int(time.time())))
            say("cloned to %s" % path)
            return str(path)

    marker = _fetch_marker(path)
    age = None
    if marker.exists():
        try:
            age = time.time() - int(marker.read_text().strip())
        except ValueError:
            age = None

    if age is None or age > max_age_seconds:
        say("fetching latest ...")
        r = _run(["git", "fetch", "--prune", "--quiet"], cwd=path, timeout=1800)
        if r.returncode == 0:
            marker.write_text(str(int(time.time())))
        else:
            # An offline run on a slightly stale clone beats no answer at all.
            say("fetch failed, continuing with the existing checkout: %s"
                % r.stderr.strip()[:200])
    else:
        say("clone is %d minute(s) old, skipping fetch" % int(age // 60))

    return str(path)
