#!/usr/bin/env python3
"""
Render the ledger for each source as a single HTML page:
- current (latest snapshot) paragraphs shown in order
- paragraphs that were ever deleted are preserved in-place with strike-through
  and a "deleted at <ts>" badge
- paragraphs added after the first snapshot are highlighted with "added at <ts>"

Output: site/<source_id>.html  (also site/index.html pointing at the first source)

Anchoring: each delete/replace-from op is recorded with the content_hash of the
paragraph immediately preceding it in the pre-delete snapshot. At render time,
the ghost is emitted right after the first live occurrence of that anchor hash.
When the anchor was itself later deleted, the ghost lands in an "orphaned
deletions" section at the bottom (still preserved, just not in-place).
"""

from __future__ import annotations

import html
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NORMALIZED_DIR = ROOT / "normalized"
DELTAS_DIR = ROOT / "deltas"
REVIEWS_DIR = ROOT / "reviews"
SITE_DIR = ROOT / "docs"

BLANK_HASH = "0" * 16


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def list_normalized(source_id: str) -> list[Path]:
    return sorted(NORMALIZED_DIR.rglob(f"*/{source_id}/*.normalized.json"))


def list_deltas(source_id: str) -> list[Path]:
    return sorted(DELTAS_DIR.rglob(f"*/{source_id}/*.delta.json"))


def list_reviews(source_id: str) -> list[Path]:
    return sorted(REVIEWS_DIR.rglob(f"*/{source_id}/*.review.json"))


def suspicious_by_ts(source_id: str) -> dict[str, list[dict]]:
    """Return {delta_ts: [suspicious_deletion concerns]} for the source."""
    out: dict[str, list[dict]] = defaultdict(list)
    for p in list_reviews(source_id):
        r = load_json(p)
        for c in r.get("concerns", []) or []:
            if c.get("type") == "suspicious_deletion":
                out[r["delta_ts"]].append(c)
    return out


def concern_matches_text(concern: dict, text: str) -> bool:
    """Fuzzy match: share a contiguous 6-char window, or one is substring of the other."""
    excerpt = (concern.get("excerpt") or "").replace("...", "").replace("…", "").strip()
    if not excerpt or not text:
        return False
    if excerpt in text or text in excerpt:
        return True
    # window-based fuzzy match to tolerate paraphrase/truncation
    for s, l in ((excerpt, text), (text, excerpt)):
        if len(s) < 6:
            continue
        for i in range(len(s) - 5):
            if s[i : i + 6] in l:
                return True
    return False


def attach_suspicious(
    ghosts_head: list[dict],
    ghosts_by_anchor: dict[str, list[dict]],
    by_ts: dict[str, list[dict]],
) -> None:
    """Mutate ghost dicts in-place: add 'suspicious_concerns' where matched.
    Unmatched concerns fall back to every ghost in the same delta (conservative)."""
    all_ghosts = list(ghosts_head) + [g for lst in ghosts_by_anchor.values() for g in lst]
    by_ts_ghosts: dict[str, list[dict]] = defaultdict(list)
    for g in all_ghosts:
        by_ts_ghosts[g["deleted_at"]].append(g)
    for ts, concerns in by_ts.items():
        ghosts_here = by_ts_ghosts.get(ts, [])
        if not ghosts_here:
            continue
        unmatched: list[dict] = []
        for c in concerns:
            matched = False
            for g in ghosts_here:
                if concern_matches_text(c, g["text"]):
                    g.setdefault("suspicious_concerns", []).append(c)
                    matched = True
            if not matched:
                unmatched.append(c)
        if unmatched:
            for g in ghosts_here:
                # only add fallback concerns where no specific match already exists
                if "suspicious_concerns" not in g:
                    g["suspicious_concerns"] = list(unmatched)


def discover_source_ids() -> list[str]:
    return sorted({p.parent.name for p in NORMALIZED_DIR.rglob("*.normalized.json")})


def is_mass_deletion(d: dict) -> bool:
    """Honor the delta's own flag; for older deltas missing it, recompute."""
    s = d.get("summary", {}) or {}
    v = s.get("mass_deletion_suspected")
    if v is not None:
        return bool(v)
    deleted = s.get("deleted_paragraphs", 0)
    from_pc = max(d.get("from", {}).get("paragraph_count", 0), 1)
    return deleted >= 10 and (deleted / from_pc) >= 0.15


def build_ghosts(norms: list[dict], deltas: list[dict]):
    """Return (ghosts_head, ghosts_by_anchor) for this source."""
    norm_by_ts = {n["captured_at_utc"]: n for n in norms}
    ghosts_head: list[dict] = []
    ghosts_by_anchor: dict[str, list[dict]] = defaultdict(list)

    for d in deltas:
        from_ts = d["from"]["captured_at_utc"]
        if from_ts not in norm_by_ts:
            continue
        from_paras = norm_by_ts[from_ts]["paragraphs"]
        to_ts = d["to"]["captured_at_utc"]
        mass_del = is_mass_deletion(d)
        for op in d["operations"]:
            if op["op"] == "delete":
                pos = op["at_from"]
                ghost_list = op["paragraphs"]
            elif op["op"] == "replace":
                pos = op["at_from"]
                ghost_list = op["from_paragraphs"]
            else:
                continue
            anchor = from_paras[pos - 1]["content_hash"] if pos > 0 else None
            for g in ghost_list:
                if g["content_hash"] == BLANK_HASH:
                    continue  # skip blank-line ghosts (too noisy)
                rec = {**g, "deleted_at": to_ts, "mass_deletion": mass_del}
                if anchor is None:
                    ghosts_head.append(rec)
                else:
                    ghosts_by_anchor[anchor].append(rec)
    return ghosts_head, ghosts_by_anchor


def first_seen_map(norms: list[dict]) -> dict[str, str]:
    fs: dict[str, str] = {}
    for n in norms:
        ts = n["captured_at_utc"]
        for p in n["paragraphs"]:
            h = p["content_hash"]
            if h == BLANK_HASH:
                continue
            if h not in fs:
                fs[h] = ts
    return fs


def esc(s: str) -> str:
    return html.escape(s).replace("\n", "<br>")


def render_live(p: dict, added_at: str | None) -> str:
    cls = "p live added" if added_at else "p live"
    badge = (
        f'<span class="badge added">+ {html.escape(added_at)}</span>'
        if added_at
        else ""
    )
    style = html.escape(p.get("style", "NORMAL_TEXT"))
    text = esc(p["text"]) or "&nbsp;"
    return f'<div class="{cls}" data-style="{style}">{badge}<div class="text">{text}</div></div>'


def render_ghost(g: dict) -> str:
    style = html.escape(g.get("style", "NORMAL_TEXT"))
    text = esc(g["text"])
    concerns = g.get("suspicious_concerns") or []
    mass = bool(g.get("mass_deletion"))
    classes = ["p", "ghost"]
    if mass:
        classes.append("mass-deletion")
    if concerns:
        classes.append("suspicious")
    badges = [f'<span class="badge deleted">− {html.escape(g["deleted_at"])}</span>']
    if mass:
        badges.append('<span class="badge mass">⛔ 批量删除</span>')
    if concerns:
        badges.append('<span class="badge flag">⚠ 可疑删除</span>')
    detail_html = ""
    if concerns:
        items = "".join(
            f'<li>{html.escape(c.get("detail", ""))}</li>' for c in concerns
        )
        detail_html = (
            f'<details class="flag-detail"><summary>AI 审查意见 ({len(concerns)})</summary>'
            f'<ul>{items}</ul></details>'
        )
    return (
        f'<div class="{" ".join(classes)}" data-style="{style}">'
        f'{"".join(badges)}'
        f'<div class="text">{text}</div>'
        f'{detail_html}'
        f'</div>'
    )


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>{title}</title>
<style>
 body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:920px;margin:2em auto;padding:0 1em;color:#222;line-height:1.5;}}
 h1{{font-size:1.5em;margin:.2em 0;}}
 .meta{{color:#666;font-size:.85em;margin-bottom:1.5em;border-bottom:1px solid #ddd;padding-bottom:1em;}}
 .legend span{{margin-right:1em;}}
 .p{{margin:.25em 0;padding:.3em .5em;border-left:3px solid transparent;}}
 .p .text{{white-space:pre-wrap;word-wrap:break-word;}}
 .p[data-style=HEADING_1] .text{{font-size:1.3em;font-weight:600;}}
 .p[data-style=HEADING_2] .text{{font-size:1.15em;font-weight:600;}}
 .p[data-style=HEADING_3] .text{{font-size:1.05em;font-weight:600;}}
 .p.ghost{{background:#fff4f4;border-left-color:#c33;}}
 .p.ghost .text{{color:#a33;text-decoration:line-through;}}
 .p.ghost.suspicious{{background:#fff6d6;border-left-color:#d4a017;box-shadow:inset 3px 0 0 #d4a017, 0 0 0 1px #d4a017;}}
 .p.ghost.suspicious .text{{color:#7a5c00;}}
 .p.ghost.mass-deletion{{background:#ffe5e5;border-left-color:#a00;box-shadow:inset 4px 0 0 #a00, 0 0 0 1px #a00;}}
 .p.ghost.mass-deletion .text{{color:#800;font-weight:500;}}
 .p.ghost.mass-deletion.suspicious{{background:#ffd9d9;}}
 .badge.mass{{background:#a00;}}
 .attack-banner{{background:#a00;color:#fff;padding:.7em 1em;border-radius:4px;margin:1em 0 1.5em;font-size:.95em;}}
 .attack-banner strong{{font-weight:600;}}
 .attack-banner code{{background:rgba(255,255,255,.2);padding:.05em .35em;border-radius:2px;}}
 .p.added{{background:#f0fff4;border-left-color:#2a8;}}
 .badge{{display:inline-block;font-size:.7em;padding:.05em .45em;border-radius:3px;margin-right:.6em;font-family:ui-monospace,monospace;vertical-align:middle;text-decoration:none;color:#fff;}}
 .badge.added{{background:#2a8;}}
 .badge.deleted{{background:#c33;}}
 .badge.flag{{background:#d4a017;}}
 .flag-detail{{margin-top:.3em;font-size:.8em;color:#555;text-decoration:none;}}
 .flag-detail summary{{cursor:pointer;color:#9a7a00;}}
 .flag-detail ul{{margin:.3em 0 .2em 1.4em;padding:0;}}
 .flag-detail li{{margin:.15em 0;}}
 h2.section{{margin-top:3em;font-size:1.1em;color:#666;border-top:1px dashed #ccc;padding-top:1em;}}
</style></head><body>
<h1>{title}</h1>
<div class="meta">
 source: <code>{source_id}</code> · snapshots: {n_snapshots} · range: {earliest_ts} → {latest_ts}<br>
 live paragraphs: {n_live} · deleted (preserved): {n_ghosts} · added since start: {n_added} · 可疑删除: {n_suspicious} · 批量删除: {n_mass}<br>
 <span class="legend"><span class="badge added">+ ts</span>added after first snapshot</span>
 <span class="legend"><span class="badge deleted">− ts</span>deleted (kept with strike-through)</span>
 <span class="legend"><span class="badge flag">⚠</span>AI 标注为可疑删除</span>
 <span class="legend"><span class="badge mass">⛔</span>单次删除超 15% / ≥10 段(疑似批量删除)</span>
</div>
<main>
{body}
</main>
</body></html>
"""


def render_source(source_id: str) -> str | None:
    norm_paths = list_normalized(source_id)
    if not norm_paths:
        return None
    norms = [load_json(p) for p in norm_paths]
    deltas = [load_json(p) for p in list_deltas(source_id)]

    latest = norms[-1]
    earliest_ts = norms[0]["captured_at_utc"]
    fs = first_seen_map(norms)
    ghosts_head, ghosts_by_anchor = build_ghosts(norms, deltas)
    attach_suspicious(ghosts_head, ghosts_by_anchor, suspicious_by_ts(source_id))

    mass_events = [
        {
            "ts": d["to"]["captured_at_utc"],
            "deleted": d["summary"].get("deleted_paragraphs", 0),
            "ratio": d["summary"].get("deletion_ratio"),
        }
        for d in deltas
        if is_mass_deletion(d)
    ]

    parts: list[str] = []
    if mass_events:
        latest_evt = mass_events[-1]
        ratio_txt = (
            f"{latest_evt['ratio']*100:.0f}%" if latest_evt.get("ratio") is not None else "?"
        )
        parts.append(
            f'<div class="attack-banner">'
            f'⛔ <strong>检测到 {len(mass_events)} 次疑似批量删除事件</strong> '
            f'(最近一次 <code>{html.escape(latest_evt["ts"])}</code>,'
            f'删除 {latest_evt["deleted"]} 段 ≈ {ratio_txt})。'
            f'所有被删内容已在下方以红色高亮保留,不会丢失。'
            f'</div>'
        )
    n_added = 0

    for g in ghosts_head:
        parts.append(render_ghost(g))

    emitted_anchor: set[str] = set()
    for p in latest["paragraphs"]:
        h = p["content_hash"]
        added_at = None
        if h != BLANK_HASH and fs.get(h) and fs[h] != earliest_ts:
            added_at = fs[h]
            n_added += 1
        parts.append(render_live(p, added_at))
        if h in ghosts_by_anchor and h not in emitted_anchor:
            emitted_anchor.add(h)
            for g in ghosts_by_anchor[h]:
                parts.append(render_ghost(g))

    orphaned = [
        g
        for h, lst in ghosts_by_anchor.items()
        if h not in emitted_anchor
        for g in lst
    ]
    if orphaned:
        parts.append('<h2 class="section">Orphaned deletions (anchor also gone)</h2>')
        for g in orphaned:
            parts.append(render_ghost(g))

    all_ghosts = ghosts_head + [x for lst in ghosts_by_anchor.values() for x in lst]
    n_ghosts = len(all_ghosts)
    n_suspicious = sum(1 for g in all_ghosts if g.get("suspicious_concerns"))
    n_mass = sum(1 for g in all_ghosts if g.get("mass_deletion"))
    return HTML_TEMPLATE.format(
        title=html.escape(latest.get("title") or source_id),
        source_id=html.escape(source_id),
        n_snapshots=len(norms),
        earliest_ts=html.escape(earliest_ts),
        latest_ts=html.escape(latest["captured_at_utc"]),
        n_live=len(latest["paragraphs"]),
        n_ghosts=n_ghosts,
        n_added=n_added,
        n_suspicious=n_suspicious,
        n_mass=n_mass,
        body="\n".join(parts),
    )


def main() -> int:
    source_ids = discover_source_ids()
    if not source_ids:
        print("no normalized snapshots yet", file=sys.stderr)
        return 1
    SITE_DIR.mkdir(exist_ok=True)
    for sid in source_ids:
        out_html = render_source(sid)
        if out_html is None:
            continue
        out = SITE_DIR / f"{sid}.html"
        out.write_text(out_html, encoding="utf-8")
        print(f"[ok] -> {out.relative_to(ROOT)}")
    # index.html points at the first source
    first = SITE_DIR / f"{source_ids[0]}.html"
    (SITE_DIR / "index.html").write_text(first.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[ok] -> {(SITE_DIR / 'index.html').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
