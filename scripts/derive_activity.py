"""Re-derive `activity_text` for the rows that need it, and nothing else.

    .venv/bin/python scripts/derive_activity.py            # do the pending work
    .venv/bin/python scripts/derive_activity.py --status    # say what is pending, change nothing

0027 moved this field from "recomputed on every read" to "derived once". The measurement that
forced it: the /search/notices ranking query was 1,778-2,040 ms warm and essentially all of it
was projecting the old view's six regexes over all 94,183 rows to decorate 20 (falsifier verdict
14 on ledger claim 17). After 0027 the same query is 1.9-5.7 ms.

WHAT MAKES A ROW PENDING, and why neither test needs to read `body_text`:

  * `rule_version` is behind the current row in `activity_rule` -- a pattern in
    `bekendmaking_activity_derive` improved. Adding a rule is a row in that table plus an
    entry in `scripts.activity_rules.EXTRACTORS`; `bekendmaking_activity_pending` reads the
    same row through `activity_rule_current()`, so the view and this script cannot disagree
    about what is current. 0027 had the number in both places and the first bump made them
    contradict each other permanently.
  * `body_etl_run` differs from `bekendmaking_body.etl_run` -- the body was re-fetched.

A digest of `body_text` would be the obvious staleness test and is the wrong one: deciding
whether it changed would mean decompressing the 72 MB TOAST relation, which is 29% of the cost
0027 removed. The run id is already there and is an integer.

THE PATTERNS ARE NOT IN THIS FILE. They are in the `bekendmaking_activity_derive` view, which
is the same expression text 0015 shipped. 0015 declined to materialise partly because a column
"would need a second copy of these patterns inside the importer, and the two would drift the
first time a pattern improved" -- so this script decides WHICH rows to run it for and never
what it does. If you find yourself writing a regex here, stop.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# `scripts.`-qualified so the same import works when this file is run directly (ROOT is on
# sys.path above) and when tests import the module.
from scripts.activity_rules import PENDING, current_rule, derive  # noqa: E402

# Small enough that one batch is a handful of index lookups rather than a seq scan, large
# enough that 94k rows is ~100 statements. The point of batching is that an interrupted run
# leaves a consistent prefix, not speed.
BATCH = 1000

async def status(conn) -> list[asyncpg.Record]:
    return await conn.fetch("""
        SELECT why, count(*) AS n FROM bekendmaking_activity_pending GROUP BY why ORDER BY why""")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--status", action="store_true", help="report and change nothing")
    ap.add_argument("--no-refresh", action="store_true",
                    help="skip REFRESH of the FTS materialized view")
    args = ap.parse_args()

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")
    conn = await asyncpg.connect(dsn)
    try:
        for r in await status(conn):
            print(f"  pending  {r['why']:14} {r['n']:,}")

        rule = await current_rule(conn)
        version, view = rule
        ids = [r["identifier"] for r in await conn.fetch(PENDING, version)]
        if not ids:
            print(f"nothing to derive (rule_version {version}, extractor view {view})")
            return
        if args.status:
            print(f"{len(ids):,} row(s) would be derived at rule_version {version} ({view})")
            return

        t0, changed = time.perf_counter(), 0
        for i in range(0, len(ids), BATCH):
            batch = ids[i:i + BATCH]
            # One transaction per batch: an interrupted run leaves a consistent prefix, and
            # the rows it did not reach are still listed by bekendmaking_activity_pending.
            async with conn.transaction():
                n = await derive(conn, batch, rule)
            changed += n
            print(f"  {min(i + BATCH, len(ids)):>7,}/{len(ids):,}  {changed:,} written", end="\r")
        print(f"\n{len(ids):,} row(s) derived, {changed:,} actually changed, "
              f"{time.perf_counter() - t0:.1f} s")

        # The FTS index is a materialized view over this text (0020), so it is stale now.
        # CONCURRENTLY because it has the unique index that allows it and because a plain
        # REFRESH takes an AccessExclusiveLock -- /bekendmakingen reads this to sort.
        if changed and not args.no_refresh:
            t0 = time.perf_counter()
            await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY bekendmaking_search_text")
            print(f"FTS materialized view refreshed, {time.perf_counter() - t0:.1f} s")
        elif changed:
            print("FTS materialized view NOT refreshed (--no-refresh): bekendmaking_search_text "
                  "is now behind bekendmaking_activity_text")

        left = await status(conn)
        print("pending after: " + (", ".join(f"{r['why']}={r['n']}" for r in left) or "nothing"))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
