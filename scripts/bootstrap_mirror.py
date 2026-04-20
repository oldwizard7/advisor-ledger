#!/usr/bin/env python3
"""One-shot: seed `MIRROR.md` from the latest normalized snapshot.

Maps Google Docs heading styles to Markdown headings:
  HEADING_1 -> #    HEADING_2 -> ##    HEADING_3 -> ###
Everything else is kept as-is. Blank paragraphs become blank lines.

Refuses to overwrite an existing MIRROR.md unless --force is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NORMALIZED_DIR = ROOT / "normalized"
MIRROR_PATH = ROOT / "MIRROR.md"

STYLE_MAP = {
    "TITLE": "# ",
    "SUBTITLE": "## ",
    "HEADING_1": "# ",
    "HEADING_2": "## ",
    "HEADING_3": "### ",
    "HEADING_4": "#### ",
    "HEADING_5": "##### ",
    "HEADING_6": "###### ",
}

PREAMBLE = """<!--
MIRROR.md — 社区维护版源文件

如果原 Google Doc 被下架,这里是下一个源。任何人都可以在 docs/ 网页(GitHub Pages)
的评论区留言(或直接开 Issue),后端的 Kimi 会定期扫描评论并把合规的内容合并到本文件,
自动 commit + push。合并后 Kimi 会在对应评论下面回复一条 "已并入 <commit-sha>"。

不要手动在 Pages 网页发言后再自己来改这里——直接评论就行,avoid race condition。
如果你要直接贡献结构性改动(比如新加一节),开 PR。
-->

"""


def latest_normalized_for(source_id: str) -> Path | None:
    paths = sorted(NORMALIZED_DIR.rglob(f"*/{source_id}/*.normalized.json"))
    return paths[-1] if paths else None


def paragraphs_to_md(paragraphs: list[dict]) -> str:
    lines: list[str] = []
    for p in paragraphs:
        text = p.get("text", "")
        style = p.get("style", "NORMAL_TEXT")
        prefix = STYLE_MAP.get(style, "")
        if not text:
            lines.append("")
            continue
        lines.append(f"{prefix}{text}")
    # collapse >2 consecutive blank lines
    out: list[str] = []
    blank_run = 0
    for ln in lines:
        if ln == "":
            blank_run += 1
            if blank_run <= 2:
                out.append(ln)
        else:
            blank_run = 0
            out.append(ln)
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="source-1")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if MIRROR_PATH.exists() and not args.force:
        print(f"{MIRROR_PATH.name} already exists; pass --force to overwrite", file=sys.stderr)
        return 1

    p = latest_normalized_for(args.source)
    if p is None:
        print(f"no normalized snapshots for source={args.source}", file=sys.stderr)
        return 1

    data = json.loads(p.read_text(encoding="utf-8"))
    md_body = paragraphs_to_md(data["paragraphs"])
    MIRROR_PATH.write_text(PREAMBLE + md_body, encoding="utf-8")
    print(
        f"wrote {MIRROR_PATH.relative_to(ROOT)} "
        f"({data['paragraph_count']} paragraphs, from {data['captured_at_utc']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
