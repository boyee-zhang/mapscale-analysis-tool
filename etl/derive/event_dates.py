"""Fill in event dates for publications whose template carries no labelled period field.

    .venv/bin/python -m etl.derive.event_dates            # rows still at no_period
    .venv/bin/python -m etl.derive.event_dates --refresh  # also re-derive ok_derived rows
    .venv/bin/python -m etl.derive.event_dates --dry-run

WHY THIS IS A SEPARATE SCRIPT AND NOT PART OF THE IMPORTER. The importer's job is to fetch a
document once; this re-reads documents already stored. Those are different costs and different
failure modes, and keeping them apart buys the property that matters here: **the extractor is
expected to improve, and a re-derivation must not mean re-fetching 34,481 documents from a
public service.** Same reasoning as the view in migration 0015 — fix a pattern, re-run, every
row is fixed. It is also why the importer's fetch path is left untouched: it works, and it
does not have the title in hand, which this extraction needs because a good number of
municipalities put the date only there.

WHAT IT WILL AND WILL NOT DO. `status='ok'` rows are never touched: those have a date the
publisher wrote into a labelled field, and a derived value must not overwrite a stated one.
Rows where the extractor returns nothing stay `no_period`, which is a real answer — measured
null rate is 36.7%, and that silence is what buys the precision.

MEASURED on 30 municipalities used in neither development nor tuning: precision 100.0%,
recall 65.5%, hallucination 0.0% (docs/pg-notes/event-dates.md). The dates this writes are
inferred from prose, not stated by the publisher, which is exactly why they land under
`status='ok_derived'` and carry `period_basis` naming the cue that found them.
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib

import asyncpg
from dotenv import dotenv_values

from etl.derive.event_dates_rules import extract

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATABASE_URL = dotenv_values(ROOT / ".env").get("DATABASE_URL")

# Scoped to event publications, and that is a correctness constraint rather than a speed one.
# The extractor was built and measured on `evenementenvergunning` bodies only. An
# omgevingsvergunning saying `het bouwen van een dakkapel` carries dates too, and nothing in
# the measurement says what this would do with them -- running it there would be applying a
# 100%-precision number outside the population it was measured on, which is how a figure
# stops meaning anything. Widen the scope when there is a labelled sample for the new family,
# not before.
_SCOPE = {
    "evenement": "b.doc_type ~* 'evenement' OR (coalesce(b.title,'') || ' ' "
                 "|| coalesce(b.abstract,'')) ~* 'evenement'",
    "all": "true",
}

SELECT = """
SELECT b.identifier, b.title, bb.body_text
FROM bekendmaking_body bb
JOIN bekendmaking b USING (identifier)
WHERE bb.status = ANY($1::text[]) AND bb.body_text IS NOT NULL AND ({scope})
ORDER BY b.identifier
"""

# Only the rows whose value actually changes, so a no-op re-run is `UPDATE 0` and does not
# rewrite 30,000 tuples for nothing -- the trap stage 1 hit on `poi` and the importer hit on
# `bekendmaking`.
UPDATE = """
UPDATE bekendmaking_body AS t SET
    status = 'ok_derived', start_date = v.start_date, end_date = v.end_date,
    period_basis = v.basis
FROM (SELECT * FROM unnest($1::text[], $2::date[], $3::date[], $4::text[])
          AS u(identifier, start_date, end_date, basis)) AS v
WHERE t.identifier = v.identifier
  AND (t.status, t.start_date, t.end_date, t.period_basis)
      IS DISTINCT FROM ('ok_derived', v.start_date, v.end_date, v.basis)
"""


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true",
                    help="also re-derive rows already at ok_derived (use after a pattern fix)")
    ap.add_argument("--family", choices=sorted(_SCOPE), default="evenement",
                    help="which publications to derive dates for; see _SCOPE")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is not set in .env")

    statuses = ["no_period", "ok_derived"] if args.refresh else ["no_period"]
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(SELECT.format(scope=_SCOPE[args.family]), statuses)
        print(f"{len(rows)} candidate row(s), family={args.family}, status {statuses}")

        idents, starts, ends, bases = [], [], [], []
        by_basis: dict[str, int] = {}
        for r in rows:
            got = extract(r["title"], r["body_text"])
            if not got["start"]:
                continue
            idents.append(r["identifier"])
            starts.append(got["start"])
            ends.append(got["end"])
            bases.append(got["basis"])
            by_basis[got["basis"]] = by_basis.get(got["basis"], 0) + 1

        print(f"  extracted a date for {len(idents)} ({len(idents) / max(len(rows), 1):.1%}); "
              f"{len(rows) - len(idents)} stay no_period")
        for b, n in sorted(by_basis.items(), key=lambda x: -x[1]):
            print(f"    {n:6}  {b}")
        if args.dry_run:
            print("--dry-run: nothing written")
            return 0
        res = await conn.execute(UPDATE, idents, starts, ends, bases)
        print(f"  {res}")

        print("\nstatus after:")
        for r in await conn.fetch(
                "SELECT status, count(*) n, count(start_date) with_date FROM bekendmaking_body "
                "GROUP BY 1 ORDER BY 2 DESC"):
            print(f"  {r['status']:16} {r['n']:7}  with a start_date: {r['with_date']}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
