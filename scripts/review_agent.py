#!/usr/bin/env python3
"""
Local (non-blocking) AI review of a delta. Calls the bitdeer Kimi K2.5
chat-completions endpoint and writes a review artifact next to the delta.

Kimi K2.5 is a reasoning model: the model emits chain-of-thought in
`reasoning_content` and the final JSON in `content`. We read `content` only
and give generous max_tokens so reasoning doesn't starve the answer.

Policy (advisory only; never blocks the commit):
  - pii: PII about students/admin/private individuals. Professors' full names
         and public academic info are NOT PII — they are the subject.
  - ad_hominem: unsupported personal insults. Criticism of concrete advising
         practices IS allowed.
  - suspicious_deletion: deletions that look like removal of substantive
         observations about a specific advisor, vs. cleanup.

Output: reviews/YYYY/MM/DD/<source_id>/<ts>.review.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DELTAS_DIR = ROOT / "deltas"
REVIEWS_DIR = ROOT / "reviews"
ENV_PATH = ROOT / "secrets" / "review_api.env"
MAX_TOKENS = 8192  # Kimi K2.5 is a reasoning model; chain-of-thought eats most of this
REQUEST_TIMEOUT = 180

SYSTEM_PROMPT = """You audit edits to a semi-public Chinese-language document about professors' advising practices (学术黑榜). Your role is a filter, not a judge. Flag exactly three issue types:

1. "pii" — personally identifying info about students, admin staff, or other private individuals: real full names of students, addresses, phone numbers, email addresses, ID numbers, or lab-specific descriptors detailed enough to de-anonymize. Professors' own full names and public academic info are NOT PII; they are the subject of this document.

2. "ad_hominem" — pure personal attacks (profanity, insults, unsupported character judgments). Criticism of concrete advising practices IS allowed; only wholly unsupported personal insults count.

3. "suspicious_deletion" — deletions that look like they could be suppressing substantive legitimate observations about a specific advisor, rather than cleanup (typos, blanks, clearly off-topic lines, dedup).

Return ONLY a JSON object and nothing else:
{"verdict": "ok" | "concerns", "concerns": [{"type": "pii" | "ad_hominem" | "suspicious_deletion", "detail": "<why>", "excerpt": "<short quote from the edit>"}]}

If nothing is flagged, return {"verdict": "ok", "concerns": []}."""


def load_env(path: Path) -> dict:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def summarize_delta(delta: dict) -> str:
    lines: list[str] = []
    for op in delta["operations"]:
        if op["op"] == "insert":
            lines.append(f"[INSERT at position {op['at_to']}]")
            for p in op["paragraphs"]:
                lines.append(f"  + {p['text']}")
        elif op["op"] == "delete":
            lines.append(f"[DELETE at position {op['at_from']}]")
            for p in op["paragraphs"]:
                lines.append(f"  - {p['text']}")
        elif op["op"] == "replace":
            lines.append(f"[REPLACE at from={op['at_from']} to={op['at_to']}]")
            for p in op["from_paragraphs"]:
                lines.append(f"  - {p['text']}")
            for p in op["to_paragraphs"]:
                lines.append(f"  + {p['text']}")
    return "\n".join(lines)


def call_chat(url: str, model: str, api_key: str, system: str, user: str) -> dict:
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": MAX_TOKENS,
            "temperature": 0.0,
            "top_p": 1.0,
            "stream": False,
        }
    ).encode("utf-8")
    # bitdeer's WAF blocks the default Python-urllib UA with 403; any custom UA works.
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "advisor-ledger/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def review_delta(delta_path: Path) -> tuple[Path, str, int]:
    env = load_env(ENV_PATH)
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    user_msg = (
        f"Document: Advisor Red Flags Notes (学术黑榜)\n"
        f"Delta: from {delta['from']['captured_at_utc']} to {delta['to']['captured_at_utc']}\n"
        f"Summary: {delta['summary']}\n\n"
        f"Changes:\n{summarize_delta(delta)}"
    )
    prompt_hash = hashlib.sha256(
        (SYSTEM_PROMPT + "\n\n" + user_msg).encode("utf-8")
    ).hexdigest()

    verdict = "error"
    concerns: list[dict] = []
    error_detail: str | None = None
    finish_reason: str | None = None
    tokens_used: int | None = None

    try:
        resp = call_chat(
            env["REVIEW_API_URL"],
            env["REVIEW_API_MODEL"],
            env["REVIEW_API_KEY"],
            SYSTEM_PROMPT,
            user_msg,
        )
        choice = resp["choices"][0]
        finish_reason = choice.get("finish_reason")
        tokens_used = resp.get("usage", {}).get("completion_tokens")
        content = choice["message"].get("content") or ""
        parsed = extract_json(content)
        if parsed is None:
            error_detail = f"could not parse JSON from content (finish_reason={finish_reason}, tokens={tokens_used})"
        else:
            verdict = parsed.get("verdict", "error")
            concerns = parsed.get("concerns", []) or []
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        error_detail = f"API transport error: {e!r}"
    except (KeyError, json.JSONDecodeError) as e:
        error_detail = f"API response shape error: {e!r}"
    except Exception as e:  # noqa: BLE001 — review must never crash pipeline
        error_detail = f"unexpected error: {e!r}"

    if error_detail:
        concerns = [
            {
                "type": "review_error",
                "detail": error_detail,
                "excerpt": "",
            }
        ]

    ts = delta["to"]["captured_at_utc"]
    out = {
        "source_id": delta["source_id"],
        "delta_path": str(delta_path.relative_to(ROOT)),
        "delta_ts": ts,
        "reviewed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": env.get("REVIEW_API_MODEL"),
        "verdict": verdict,
        "concerns": concerns,
        "prompt_sha256": prompt_hash,
        "finish_reason": finish_reason,
        "completion_tokens": tokens_used,
    }
    out_path = (
        REVIEWS_DIR
        / ts[:4]
        / ts[5:7]
        / ts[8:10]
        / delta["source_id"]
        / f"{ts}.review.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_path, verdict, len(concerns)


def latest_delta(source_id: str) -> Path | None:
    deltas = sorted(DELTAS_DIR.rglob(f"*/{source_id}/*.delta.json"))
    return deltas[-1] if deltas else None


def review_path_for_delta(delta_path: Path) -> Path:
    # mirror the YYYY/MM/DD/source_id path under reviews/
    rel = delta_path.relative_to(DELTAS_DIR)
    return REVIEWS_DIR / rel.with_name(rel.stem.replace(".delta", "") + ".review.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", metavar="SOURCE_ID")
    ap.add_argument("--skip-if-exists", action="store_true")
    ap.add_argument("delta_path", nargs="?")
    args = ap.parse_args()

    if args.latest:
        d = latest_delta(args.latest)
        if d is None:
            print(f"no deltas for {args.latest}", file=sys.stderr)
            return 0
        delta_path = d
    elif args.delta_path:
        delta_path = Path(args.delta_path).resolve()
    else:
        ap.error("provide --latest SOURCE_ID or a delta path")

    # idempotency: skip if we already reviewed this delta
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    ts = delta["to"]["captured_at_utc"]
    expected = (
        REVIEWS_DIR
        / ts[:4]
        / ts[5:7]
        / ts[8:10]
        / delta["source_id"]
        / f"{ts}.review.json"
    )
    if args.skip_if_exists and expected.exists():
        print(f"[skip] {expected.relative_to(ROOT)} exists")
        return 0

    out, verdict, n = review_delta(delta_path)
    print(
        f"[ok] {delta_path.name}: verdict={verdict} concerns={n} -> {out.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
