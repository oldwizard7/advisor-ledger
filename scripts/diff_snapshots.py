#!/usr/bin/env python3
"""
Phase 2 differ: compare two normalized snapshots (or the latest pair for a
source) and write a delta file.

Output: deltas/YYYY/MM/DD/<source_id>/<ts_to>.delta.json

Shape:
{
  "source_id", "google_doc_id",
  "from": {"captured_at_utc", "revision_id", "paragraph_count"},
  "to":   {"captured_at_utc", "revision_id", "paragraph_count"},
  "summary": {"inserted": N, "deleted": M, "unchanged_blocks": K, "changed": bool},
  "operations": [
    {"op": "insert", "at_to": 42,
     "paragraphs": [{"content_hash","style","text"}, ...]},
    {"op": "delete", "at_from": 41,
     "paragraphs": [{"content_hash","style","text"}, ...]},
    {"op": "replace", "at_from": 10, "at_to": 10,
     "from_paragraphs": [...], "to_paragraphs": [...]},
    ...
  ]
}

Usage:
  diff_snapshots.py --latest <source_id>       # diff the two most recent
                                               # normalized snapshots for a source
  diff_snapshots.py <from.normalized.json> <to.normalized.json>
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NORMALIZED_DIR = ROOT / "normalized"
DELTAS_DIR = ROOT / "deltas"


def load_normalized(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_snapshots_for_source(source_id: str) -> list[Path]:
    return sorted(NORMALIZED_DIR.rglob(f"*/{source_id}/*.normalized.json"))


def para_summary(p: dict) -> dict:
    return {
        "content_hash": p["content_hash"],
        "style": p["style"],
        "text": p["text"],
    }


def build_operations(from_paras: list[dict], to_paras: list[dict]) -> list[dict]:
    # Match on content_hash so unchanged text doesn't show up as churn.
    a = [p["content_hash"] for p in from_paras]
    b = [p["content_hash"] for p in to_paras]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ops: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            ops.append(
                {
                    "op": "insert",
                    "at_to": j1,
                    "paragraphs": [para_summary(p) for p in to_paras[j1:j2]],
                }
            )
        elif tag == "delete":
            ops.append(
                {
                    "op": "delete",
                    "at_from": i1,
                    "paragraphs": [para_summary(p) for p in from_paras[i1:i2]],
                }
            )
        elif tag == "replace":
            ops.append(
                {
                    "op": "replace",
                    "at_from": i1,
                    "at_to": j1,
                    "from_paragraphs": [para_summary(p) for p in from_paras[i1:i2]],
                    "to_paragraphs": [para_summary(p) for p in to_paras[j1:j2]],
                }
            )
    return ops


# Structural attack heuristic: flag when a single diff removes (or inserts)
# an unusually large chunk of the doc — e.g. vandalism that blanks the whole
# page, or a copy-paste flood. Tuned to avoid firing on normal editing.
MASS_ABS_THRESHOLD = 10      # at least this many paragraphs touched
MASS_RATIO_THRESHOLD = 0.15  # AND at least this fraction of the prior/new size


def compute_delta(from_norm: dict, to_norm: dict) -> dict:
    ops = build_operations(from_norm["paragraphs"], to_norm["paragraphs"])
    inserted = sum(len(o.get("paragraphs", o.get("to_paragraphs", []))) for o in ops if o["op"] in ("insert", "replace"))
    deleted = sum(len(o.get("paragraphs", o.get("from_paragraphs", []))) for o in ops if o["op"] in ("delete", "replace"))
    from_count = max(from_norm["paragraph_count"], 1)
    to_count = max(to_norm["paragraph_count"], 1)
    mass_deletion = (
        deleted >= MASS_ABS_THRESHOLD
        and (deleted / from_count) >= MASS_RATIO_THRESHOLD
    )
    mass_insertion = (
        inserted >= MASS_ABS_THRESHOLD
        and (inserted / to_count) >= MASS_RATIO_THRESHOLD
    )
    return {
        "source_id": to_norm["source_id"],
        "google_doc_id": to_norm["google_doc_id"],
        "from": {
            "captured_at_utc": from_norm["captured_at_utc"],
            "revision_id": from_norm.get("revision_id"),
            "paragraph_count": from_norm["paragraph_count"],
        },
        "to": {
            "captured_at_utc": to_norm["captured_at_utc"],
            "revision_id": to_norm.get("revision_id"),
            "paragraph_count": to_norm["paragraph_count"],
        },
        "summary": {
            "inserted_paragraphs": inserted,
            "deleted_paragraphs": deleted,
            "operations": len(ops),
            "changed": len(ops) > 0,
            "mass_deletion_suspected": mass_deletion,
            "mass_insertion_suspected": mass_insertion,
            "deletion_ratio": round(deleted / from_count, 4),
            "insertion_ratio": round(inserted / to_count, 4),
        },
        "operations": ops,
    }


def delta_out_path(to_norm: dict) -> Path:
    ts = to_norm["captured_at_utc"]
    return (
        DELTAS_DIR
        / ts[:4]
        / ts[5:7]
        / ts[8:10]
        / to_norm["source_id"]
        / f"{ts}.delta.json"
    )


def write_delta(delta: dict, to_norm: dict) -> Path:
    out = delta_out_path(to_norm)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(delta, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", metavar="SOURCE_ID", help="diff most recent pair for source")
    ap.add_argument("from_path", nargs="?")
    ap.add_argument("to_path", nargs="?")
    args = ap.parse_args()

    if args.latest:
        snaps = list_snapshots_for_source(args.latest)
        if len(snaps) < 2:
            print(f"need >=2 normalized snapshots for {args.latest}, have {len(snaps)}", file=sys.stderr)
            return 1
        from_path, to_path = snaps[-2], snaps[-1]
    elif args.from_path and args.to_path:
        from_path, to_path = Path(args.from_path), Path(args.to_path)
    else:
        ap.error("provide --latest SOURCE_ID, or two normalized paths")

    from_norm = load_normalized(from_path)
    to_norm = load_normalized(to_path)
    delta = compute_delta(from_norm, to_norm)
    out = write_delta(delta, to_norm)
    s = delta["summary"]
    print(
        f"[ok] {from_path.name} -> {to_path.name}: "
        f"{s['operations']} ops, +{s['inserted_paragraphs']} / -{s['deleted_paragraphs']} "
        f"-> {out.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
