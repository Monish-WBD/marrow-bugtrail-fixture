#!/usr/bin/env python3
"""Render a Markdown document to a styled, colourful PDF.

Standard library only. Markdown is converted to HTML here rather than with a
third-party package, then headless Chrome prints it - Chrome is used because it
supports the full CSS needed for syntax colouring and page-break control.

Usage:
    python3 tools/docs/md2pdf.py BUGTRAIL.md docs/BUGTRAIL.pdf
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]

KEYWORDS = {
    "bash": r"\b(cd|echo|for|do|done|if|then|fi|set|export|git|python3|test|rm|mkdir|cp|mv|chmod|command|local|return|exit)\b",
    "python": r"\b(def|class|return|import|from|if|elif|else|for|while|try|except|raise|with|as|in|not|and|or|None|True|False|lambda|yield)\b",
    "json": r"\b(true|false|null)\b",
    "sql": r"\b(SELECT|FROM|WHERE|AND|OR|NOT|ORDER BY|project|issuetype|comment|created)\b",
    "kotlin": r"\b(fun|val|var|class|return|if|else|when|import|package|private|public|data|object|List|Boolean|Long)\b",
    "swift": r"\b(func|let|var|class|struct|enum|return|if|else|guard|import|public|private|final|self|true|false|nil|TimeInterval|Bool)\b",
}

STATUS_COLOURS = {"✅": "ok", "⚠️": "warn", "❌": "bad"}


# --------------------------------------------------------------------------
# Inline formatting
# --------------------------------------------------------------------------

def inline(text: str) -> str:
    """Convert inline markdown. Code spans are protected before other rules."""
    spans: list = []

    def stash(m):
        spans.append(html.escape(m.group(1)))
        return "\x00%d\x00" % (len(spans) - 1)

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text)

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<em>\1</em>", text)

    for mark, cls in STATUS_COLOURS.items():
        text = text.replace(mark, '<span class="mark %s">%s</span>' % (cls, mark))

    def restore(m):
        return '<code>%s</code>' % spans[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", restore, text)


# --------------------------------------------------------------------------
# Code highlighting
# --------------------------------------------------------------------------

def highlight(code: str, lang: str) -> str:
    """Wrap comments, strings, numbers and keywords in coloured spans.

    Runs over the raw text and escapes inside the callback, so escaping never
    interferes with the patterns.
    """
    comment = r"(?://[^\n]*|\#[^\n]*|/\*.*?\*/)"
    string = r"""(?:"[^"\n]*"|'[^'\n]*')"""
    number = r"\b\d+(?:\.\d+)?\b"
    keyword = KEYWORDS.get(lang, "")

    parts = [("comment", comment), ("string", string)]
    if keyword:
        parts.append(("keyword", keyword))
    parts.append(("number", number))

    pattern = re.compile(
        "|".join("(?P<%s>%s)" % (name, expr) for name, expr in parts),
        re.DOTALL,
    )

    out, pos = [], 0
    for m in pattern.finditer(code):
        out.append(html.escape(code[pos : m.start()]))
        out.append(
            '<span class="tok-%s">%s</span>'
            % (m.lastgroup, html.escape(m.group()))
        )
        pos = m.end()
    out.append(html.escape(code[pos:]))
    return "".join(out)


# --------------------------------------------------------------------------
# Block parsing
# --------------------------------------------------------------------------

def convert(md: str) -> str:
    lines = md.split("\n")
    out: list = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Fenced code block
        if line.startswith("```"):
            lang = line[3:].strip().lower()
            body = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            code = "\n".join(body)
            rendered = highlight(code, lang) if lang else html.escape(code)
            label = '<span class="lang">%s</span>' % lang if lang else ""
            # Tall blocks may split across pages; keeping them whole would leave
            # most of a page blank.
            cls = "codewrap long" if len(body) > 22 else "codewrap"
            out.append('<div class="%s">%s<pre><code>%s</code></pre></div>'
                       % (cls, label, rendered))
            continue

        # Table
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1]):
            header = [c.strip() for c in line.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>")
            out.extend("<th>%s</th>" % inline(c) for c in header)
            out.append("</tr></thead><tbody>")
            for row in rows:
                out.append("<tr>")
                out.extend("<td>%s</td>" % inline(c) for c in row)
                out.append("</tr>")
            out.append("</tbody></table>")
            continue

        # Heading
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            text = m.group(2)
            anchor = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
            out.append('<h%d id="%s">%s</h%d>' % (level, anchor, inline(text), level))
            i += 1
            continue

        # Blockquote
        if line.startswith(">"):
            body = []
            while i < len(lines) and lines[i].startswith(">"):
                body.append(lines[i].lstrip(">").strip())
                i += 1
            joined = " ".join(b for b in body if b)
            cls = "callout"
            if "⚠️" in joined or "not" == joined[:3].lower():
                cls = "callout warnbox"
            out.append('<blockquote class="%s">%s</blockquote>' % (cls, inline(joined)))
            continue

        # Horizontal rule
        if re.match(r"^-{3,}$", line.strip()):
            out.append('<hr/>')
            i += 1
            continue

        # Lists
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join("<li>%s</li>" % inline(x) for x in items) + "</ul>")
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>" + "".join("<li>%s</li>" % inline(x) for x in items) + "</ol>")
            continue

        # Raw HTML passthrough
        if line.startswith("<"):
            out.append(line)
            i += 1
            continue

        # Paragraph
        if line.strip():
            body = []
            while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|```|\||>|-{3,}$|\s*[-*]\s|\s*\d+\.\s|<)", lines[i]
            ):
                body.append(lines[i])
                i += 1
            out.append("<p>%s</p>" % inline(" ".join(body)))
            continue

        i += 1

    return "\n".join(out)


CSS = """
:root {
  --ink:#1a1d29; --muted:#5b6478; --line:#e3e7ef;
  --brand:#5b3df5; --brand2:#00b6a6; --accent:#ff6b9d;
  --ok:#12a150; --warn:#d97706; --bad:#dc2626;
  --code-bg:#171a2b;
}
* { box-sizing:border-box; }
body {
  font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
  color:var(--ink); margin:0; padding:0 46px 46px;
  -webkit-print-color-adjust:exact; print-color-adjust:exact;
}
.cover {
  background:linear-gradient(135deg,#5b3df5 0%,#8b5cf6 45%,#00b6a6 100%);
  color:#fff; margin:0 -46px 34px; padding:64px 46px 52px;
}
.cover h1 { font-size:46px; margin:0 0 10px; color:#fff; border:0; letter-spacing:-1px; }
.cover .tag { font-size:17px; opacity:.95; margin:0 0 26px; max-width:640px; }
.badges { display:flex; flex-wrap:wrap; gap:8px; }
.badge {
  background:rgba(255,255,255,.18); border:1px solid rgba(255,255,255,.35);
  padding:5px 13px; border-radius:20px; font-size:11.5px; font-weight:600;
  letter-spacing:.3px;
}
h1,h2,h3,h4 { line-height:1.25; margin:1.9em 0 .6em; }
h1 { font-size:27px; color:var(--brand); border-bottom:3px solid var(--brand);
     padding-bottom:7px; page-break-before:always; }
h1:first-of-type { page-break-before:avoid; }
h2 { font-size:21px; padding-left:12px; border-left:5px solid var(--brand2); }
h3 { font-size:16.5px; color:#33384a; }
h4 { font-size:14.5px; color:var(--muted); text-transform:uppercase;
     letter-spacing:.6px; }
p { margin:.7em 0; }
a { color:var(--brand); text-decoration:none; border-bottom:1px solid #d6cdfd; }
hr { border:0; border-top:1px dashed var(--line); margin:26px 0; }
ul,ol { margin:.6em 0; padding-left:24px; }
li { margin:.28em 0; }
code {
  background:#f3f0ff; color:#6d3bf0; padding:1.5px 6px; border-radius:5px;
  font:12px/1.5 "SF Mono",Menlo,Consolas,monospace; border:1px solid #e7e0ff;
}
.codewrap { position:relative; margin:15px 0; page-break-inside:avoid; }
.codewrap.long { page-break-inside:auto; }
.codewrap.long pre { page-break-inside:auto; }
.lang {
  position:absolute; top:0; right:0; background:var(--brand);
  color:#fff; font-size:9.5px; font-weight:700; letter-spacing:1px;
  text-transform:uppercase; padding:3px 10px; border-radius:0 8px 0 8px;
}
pre {
  background:var(--code-bg); color:#e6e9f5; padding:15px 17px; border-radius:8px;
  overflow:hidden; margin:0; border-left:4px solid var(--brand2);
}
pre code {
  background:none; border:0; color:inherit; padding:0; font-size:11.5px;
  white-space:pre-wrap; word-break:break-word;
}
.tok-comment { color:#7d86a8; font-style:italic; }
.tok-string  { color:#8ee98a; }
.tok-keyword { color:#ff9ec7; font-weight:600; }
.tok-number  { color:#ffc978; }
table {
  border-collapse:collapse; width:100%; margin:15px 0; font-size:12.5px;
  page-break-inside:avoid; border-radius:8px; overflow:hidden;
  box-shadow:0 0 0 1px var(--line);
}
thead th {
  background:linear-gradient(135deg,var(--brand),#7c5cf7); color:#fff;
  text-align:left; padding:9px 12px; font-size:11.5px; font-weight:700;
  letter-spacing:.4px; text-transform:uppercase;
}
td { padding:8px 12px; border-top:1px solid var(--line); vertical-align:top; }
tbody tr:nth-child(even) { background:#faf9ff; }
blockquote {
  margin:15px 0; padding:12px 16px; border-left:4px solid var(--brand2);
  background:#eefbfa; border-radius:0 8px 8px 0; color:#134e4a;
  page-break-inside:avoid;
}
blockquote.warnbox { border-left-color:var(--warn); background:#fff8ed; color:#7c3a00; }
.mark { font-weight:700; }
.mark.ok  { color:var(--ok); }
.mark.warn{ color:var(--warn); }
.mark.bad { color:var(--bad); }
sub { font-size:11.5px; color:var(--muted); }
@page { size:A4; margin:14mm 0; }
"""


def build_html(md: str, title: str, subtitle: str, badges: list) -> str:
    body = convert(md)
    # The source document's own H1 becomes the cover, so drop it from the flow.
    body = re.sub(r"<h1[^>]*>.*?</h1>", "", body, count=1, flags=re.DOTALL)
    chips = "".join('<span class="badge">%s</span>' % html.escape(b) for b in badges)
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>%s</title><style>%s</style></head>
<body>
<div class="cover">
  <h1>%s</h1>
  <p class="tag">%s</p>
  <div class="badges">%s</div>
</div>
%s
</body></html>""" % (html.escape(title), CSS, html.escape(title),
                     html.escape(subtitle), chips, body)


DEFAULT_TITLE = "BugTrail"
DEFAULT_SUBTITLE = (
    "A bug report goes in. A named pull request, its author, the suspect "
    "diff, the owning team, and a proven failing regression test come out."
)
DEFAULT_BADGES = [
    "Team Documentation",
    "Python 3.9+ · stdlib only",
    "No network · no credentials",
    "15 tests passing",
    "Attribution mechanically verified",
]


def main() -> int:
    # The cover text used to be written into this function, which meant a second
    # document could only be rendered by editing the renderer. Flags instead, with
    # the original wording as the defaults so existing invocations are unchanged.
    ap = argparse.ArgumentParser(description="Render markdown to a styled PDF")
    ap.add_argument("src")
    ap.add_argument("dest")
    ap.add_argument("--title", default=DEFAULT_TITLE)
    ap.add_argument("--subtitle", default=DEFAULT_SUBTITLE)
    ap.add_argument("--badge", action="append", dest="badges",
                    help="repeatable; replaces the default set entirely")
    args = ap.parse_args()

    src, dest = Path(args.src), Path(args.dest)
    md = src.read_text()

    chrome = next((c for c in CHROME_CANDIDATES if Path(c).exists()), None)
    if chrome is None:
        print("No Chrome/Edge/Chromium found; cannot render PDF.", file=sys.stderr)
        return 1

    page = build_html(
        md,
        title=args.title,
        subtitle=args.subtitle,
        badges=args.badges or DEFAULT_BADGES,
    )

    build = Path(".build/pdf")
    build.mkdir(parents=True, exist_ok=True)
    html_path = build / (src.stem + ".html")
    html_path.write_text(page)

    dest.parent.mkdir(parents=True, exist_ok=True)
    profile = build / "chrome-profile"

    if dest.exists():
        dest.unlink()

    # Headless Chrome writes the PDF and then lingers, so it is killed on a
    # timeout and success is judged by the file appearing on disk.
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-first-run",
            "--no-pdf-header-footer",
            "--user-data-dir=%s" % profile,
            "--virtual-time-budget=8000",
            "--print-to-pdf=%s" % dest.resolve(),
            html_path.resolve().as_uri(),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    written, last_size = False, -1
    for _ in range(120):
        if proc.poll() is not None:
            break
        size = dest.stat().st_size if dest.exists() else 0
        if size > 0 and size == last_size:
            written = True
            break
        last_size = size
        time.sleep(0.5)

    proc.kill()
    proc.communicate()

    if not (written or (dest.exists() and dest.stat().st_size > 0)):
        print("Chrome did not produce a PDF.", file=sys.stderr)
        return 1

    shutil.rmtree(profile, ignore_errors=True)
    print("Wrote %s (%.0f KB)" % (dest, dest.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
