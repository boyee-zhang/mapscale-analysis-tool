"""Re-derive `activity_text` for the rows that need it, and nothing else.

    .venv/bin/python scripts/derive_activity.py            # do the pending work
    .venv/bin/python scripts/derive_activity.py --status    # say what is pending, change nothing

0027 moved this field from "recomputed on every read" to "derived once". The measurement that
forced it: the /search/notices ranking query was 1,778-2,040 ms warm and essentially all of it
was projecting the old view's six regexes over all 94,183 rows to decorate 20 (falsifier verdict
14 on ledger claim 17). After 0027 the same query is 1.9-5.7 ms.

WHAT MAKES A ROW PENDING, and why neither test needs to read `body_text`:

  * `rule_version` is behind RULE_VERSION below -- a pattern in `bekendmaking_activity_derive`
    improved. Bump the constant in the same commit that changes the view, or the improvement
    silently applies to new rows only and the corpus becomes a mix of two rule sets with
    nothing saying so.
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
import time

import asyncpg
from dotenv import load_dotenv

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Must match the value 0027 backfilled with, and be bumped whenever a pattern in
# `bekendmaking_activity_derive` changes.
RULE_VERSION = 1

# Small enough that one batch is a handful of index lookups rather than a seq scan, large
# enough that 94k rows is ~100 statements. The point of batching is that an interrupted run
# leaves a consistent prefix, not speed.
BATCH = 1000

_PENDING = """
SELECT b.identifier
FROM bekendmaking b
LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
LEFT JOIN bekendmaking_activity_text t ON t.identifier = b.identifier
WHERE t.identifier IS NULL
   OR t.body_etl_run IS DISTINCT FROM bb.etl_run
   OR t.rule_version <> $1
ORDER BY b.identifier
"""

# One batch. The derive view is read for these identifiers only, so the regexes and the TOAST
# decompression happen BATCH times, not 94,183 times.
_DERIVE = """
INSERT INTO bekendmaking_activity_text
       (identifier, activity_text, activity_src, rule_version, body_etl_run, derived_at)
SELECT d.identifier, d.activity_text, d.activity_src, $2, bb.etl_run, now()
FROM bekendmaking_activity_derive d
LEFT JOIN bekendmaking_body bb ON bb.identifier = d.identifier
WHERE d.identifier = ANY($1::text[])
ON CONFLICT (identifier) DO UPDATE SET
    activity_text = EXCLUDED.activity_text,
    activity_src  = EXCLUDED.activity_src,
    rule_version  = EXCLUDED.rule_version,
    body_etl_run  = EXCLUDED.body_etl_run,
    derived_at    = EXCLUDED.derived_at
WHERE bekendmaking_activity_text.activity_text IS DISTINCT FROM EXCLUDED.activity_text
   OR bekendmaking_activity_text.activity_src  IS DISTINCT FROM EXCLUDED.activity_src
   OR bekendmaking_activity_text.rule_version  IS DISTINCT FROM EXCLUDED.rule_version
   OR bekendmaking_activity_text.body_etl_run  IS DISTINCT FROM EXCLUDED.body_etl_run
RETURNING identifier
"""


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

        ids = [r["identifier"] for r in await conn.fetch(_PENDING, RULE_VERSION)]
        if not ids:
            print(f"nothing to derive (rule_version {RULE_VERSION})")
            return
        if args.status:
            print(f"{len(ids):,} row(s) would be derived at rule_version {RULE_VERSION}")
            return

        t0, changed = time.perf_counter(), 0
        for i in range(0, len(ids), BATCH):
            batch = ids[i:i + BATCH]
            # One transaction per batch: an interrupted run leaves a consistent prefix, and
            # the rows it did not reach are still listed by bekendmaking_activity_pending.
            async with conn.transaction():
                rows = await conn.fetch(_DERIVE, batch, RULE_VERSION)
            changed += len(rows)
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
