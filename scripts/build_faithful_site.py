#!/usr/bin/env python3
"""Build the 原文视图 site: a per-snapshot archive under docs/faithful/ plus the
default landing page at docs/index.html, all sharing a top-bar snapshot selector.

Selection rules (edit SNAPSHOTS below, oldest first, to change the set):
  1. One snapshot roughly every 2 hours.
  2. The newest and oldest snapshots are always kept, regardless of rule 1.
  3. The newest entry in SNAPSHOTS is rendered as docs/index.html — i.e. the
     default view at the site root. Keep it at the last known-good capture
     (e.g. the last version before the Google Doc was vandalized).

Output:
  docs/faithful/<ts>.html    — one page per snapshot with selector + prev/next
  docs/faithful/index.html   — mirror of the newest snapshot page
  docs/index.html            — same content as the newest page, but with hrefs
                               rewritten so the selector works from site root

The three-view nav (原文 / 编辑历史 / 去重视图) is injected by the renderer and
links to ledger.html / deduped.html, which remain the responsibility of
render_ledger.py (unchanged).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RENDERER = ROOT / "scripts" / "render_gdoc_faithful.py"
DOCS_DIR = ROOT / "docs"
OUT_DIR = DOCS_DIR / "faithful"

# Oldest first. The newest entry becomes index.html and is marked "最新".
SNAPSHOTS: list[dict] = [
    {
        "ts": "2026-04-19T21-32-57Z",
        "path": "snapshots/2026/04/19/source-1/2026-04-19T21-32-57Z.json",
    },
    {
        "ts": "2026-04-19T23-03-40Z",
        "path": "snapshots/2026/04/19/source-1/2026-04-19T23-03-40Z.json",
    },
    {
        "ts": "2026-04-20T01-02-42Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T01-02-42Z.json",
    },
    {
        "ts": "2026-04-20T03-00-40Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T03-00-40Z.json",
    },
    {
        "ts": "2026-04-20T05-02-44Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T05-02-44Z.json",
    },
    {
        "ts": "2026-04-20T07-03-33Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T07-03-33Z.json",
    },
    {
        "ts": "2026-04-20T09-03-50Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T09-03-50Z.json",
    },
    {
        "ts": "2026-04-20T11-03-35Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T11-03-35Z.json",
    },
    {
        "ts": "2026-04-20T13-01-52Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T13-01-52Z.json",
    },
    {
        "ts": "2026-04-20T15-03-00Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T15-03-00Z.json",
    },
    {
        "ts": "2026-04-20T17-00-50Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T17-00-50Z.json",
    },
    {
        "ts": "2026-04-20T19-03-30Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T19-03-30Z.json",
    },
    {
        "ts": "2026-04-20T21-01-30Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T21-01-30Z.json",
    },
    {
        "ts": "2026-04-20T23-01-10Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T23-01-10Z.json",
    },
    {
        "ts": "2026-04-20T23-33-20Z",
        "path": "snapshots/2026/04/20/source-1/2026-04-20T23-33-20Z.json",
    },
]


def pretty(ts: str) -> str:
    """'2026-04-21T01-05-00Z' -> '2026-04-21 01:05:00 UTC'"""
    date, rest = ts.split("T", 1)
    return f"{date} {rest[:-1].replace('-', ':')} UTC"


def main() -> int:
    if not SNAPSHOTS:
        print("SNAPSHOTS is empty; nothing to build", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Two nav variants, since hrefs must resolve correctly from each page's URL:
    #   - nav_faithful: used on /faithful/<ts>.html (hrefs are siblings).
    #   - nav_root:     used on /index.html (hrefs nest into faithful/).
    nav_faithful, nav_root = [], []
    for i, s in enumerate(reversed(SNAPSHOTS)):
        label = pretty(s["ts"]) + (" (最新)" if i == 0 else "")
        nav_faithful.append({"ts": s["ts"], "href": f"{s['ts']}.html", "label": label})
        nav_root.append({"ts": s["ts"], "href": f"faithful/{s['ts']}.html", "label": label})
    nav_faithful_json = json.dumps(nav_faithful, ensure_ascii=False)
    nav_root_json = json.dumps(nav_root, ensure_ascii=False)

    for s in SNAPSHOTS:
        src = ROOT / s["path"]
        if not src.exists():
            print(f"[skip] {src} not found", file=sys.stderr)
            continue
        out = OUT_DIR / f"{s['ts']}.html"
        subprocess.check_call([
            sys.executable, str(RENDERER), str(src), str(out),
            "--nav-snapshots", nav_faithful_json,
            "--current-ts", s["ts"],
            "--view-nav-prefix", "../",
        ])

    latest = SNAPSHOTS[-1]
    latest_html = OUT_DIR / f"{latest['ts']}.html"
    (OUT_DIR / "index.html").write_text(
        latest_html.read_text(encoding="utf-8"), encoding="utf-8"
    )
    print(f"[ok] faithful/index.html mirrors {latest['ts']}")

    # Also render docs/index.html (the site root = Google Doc 原文 default view)
    # from the newest snapshot, with the snapshot selector baked in.
    root_src = ROOT / latest["path"]
    subprocess.check_call([
        sys.executable, str(RENDERER), str(root_src), str(DOCS_DIR / "index.html"),
        "--nav-snapshots", nav_root_json,
        "--current-ts", latest["ts"],
        "--view-nav-prefix", "",
    ])
    print(f"[ok] docs/index.html rendered from {latest['ts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
