"""Measure what including <tussenkop> and Dutch month-name dates actually changes.

Two questions, and the second matters more:
  1. how many `no_period` rows gain a period?
  2. do any rows that ALREADY parsed change? A fix that recovers 10,000 rows and silently
     moves one correct date is not a fix.

    .venv/bin/python scripts/experiments/tussenkop_before_after.py --per-stratum 100

Re-fetches the documents; it never writes to the database.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

import asyncpg
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from etl.ingest.bekendmaking_body import DOC, UA, parse_body  # noqa: E402

STRATA = {
    "no_period / Amsterdam": "bb.status='no_period' AND b.gemeente='Amsterdam'",
    "no_period / elders": "bb.status='no_period' AND b.gemeente IS DISTINCT FROM 'Amsterdam'",
    "ok / Amsterdam": "bb.status='ok' AND b.gemeente='Amsterdam'",
    "ok / elders": "bb.status='ok' AND b.gemeente IS DISTINCT FROM 'Amsterdam'",
    "ok_derived / any": "bb.status='ok_derived'",
}
SEED = "20261112"


async def fetch_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, row: dict) -> dict:
    async with sem:
        try:
            r = await client.get(DOC.format(row["identifier"]), headers={"User-Agent": UA})
            if r.status_code != 200:
                return {**row, "new_status": f"http_{r.status_code}"}
            xml = r.text
        except httpx.HTTPError as e:
            return {**row, "new_status": "fetch_error", "err": type(e).__name__}
        await asyncio.sleep(0.12)
    new = parse_body(xml)
    return {**row,
             "new_status": new["status"],
             "new_start": new["start_date"].isoformat() if new.get("start_date") else None,
             "new_end": new["end_date"].isoformat() if new.get("end_date") else None,
             "new_raw": new.get("period_raw")}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-stratum", type=int, default=100)
    ap.add_argument("--out", default="/tmp/tussenkop-before-after.json")
    args = ap.parse_args()

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(dsn)
    picked: list[dict] = []
    try:
        for name, pred in STRATA.items():
            rows = await conn.fetch(f"""
                SELECT bb.identifier, bb.status AS old_status, b.gemeente, b.doc_type,
                       bb.start_date AS old_start, bb.end_date AS old_end
                FROM bekendmaking_body bb JOIN bekendmaking b USING (identifier)
                WHERE {pred}
                ORDER BY md5(bb.identifier || '{SEED}') LIMIT {args.per_stratum}""")
            if not rows:
                print(f"FATAL: stratum {name!r} selected 0 rows — that is a query bug, "
                      f"not a finding", file=sys.stderr)
                return 1
            for r in rows:
                picked.append({
                    "stratum": name, "identifier": r["identifier"],
                    "old_status": r["old_status"], "gemeente": r["gemeente"],
                    "doc_type": r["doc_type"],
                    "old_start": r["old_start"].isoformat() if r["old_start"] else None,
                    "old_end": r["old_end"].isoformat() if r["old_end"] else None})
            print(f"  picked {len(rows):>4} from {name}")
    finally:
        await conn.close()

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        sem = asyncio.Semaphore(5)
        out = await asyncio.gather(*[fetch_one(client, sem, r) for r in picked])
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False))

    print(f"\n{'stratum':24} {'n':>4} {'gained':>7} {'lost':>5} {'changed':>8} {'same':>5} {'failed':>7}")
    print("-" * 68)
    regressions = []
    for name in STRATA:
        rows = [r for r in out if r["stratum"] == name]
        gained = lost = changed = same = failed = 0
        for r in rows:
            if r["new_status"] in ("fetch_error",) or r["new_status"].startswith("http_"):
                failed += 1
                continue
            had = r["old_status"] in ("ok", "ok_derived")
            has = r["new_status"] in ("ok", "ok_derived")
            if not had and has:
                gained += 1
            elif had and not has:
                lost += 1
                regressions.append(("LOST PERIOD", r))
            elif had and has and (r["old_start"] != r["new_start"]
                                  or r["old_end"] != r["new_end"]):
                changed += 1
                regressions.append(("DATE CHANGED", r))
            else:
                same += 1
        print(f"{name:24} {len(rows):>4} {gained:>7} {lost:>5} {changed:>8} {same:>5} {failed:>7}")

    print(f"\nnew status distribution: {dict(Counter(r['new_status'] for r in out))}")
    if regressions:
        print(f"\n!!! {len(regressions)} ROWS CHANGED THAT ALREADY PARSED — inspect every one:")
        for kind, r in regressions[:20]:
            print(f"  [{kind}] {r['identifier']} ({r['gemeente']}, {r['doc_type']})")
            print(f"      old {r['old_status']} {r['old_start']}..{r['old_end']}")
            print(f"      new {r['new_status']} {r.get('new_start')}..{r.get('new_end')}")
            print(f"      raw {str(r.get('new_raw'))[:110]}")
    else:
        print("\nno row that already parsed changed its status or its dates.")
    print(f"\nraw: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
