#!/usr/bin/env python3
"""Load the noise ground truth (frame + labels) from docs/pg-notes/*.csv into Postgres.

Why the data lives in CSV and not in a numbered migration, which is where this repo keeps
its other seed data (0004 poi_category_seed, 0012 noise_impact_rule_seed):

  `noise_frame_member` and `noise_label` both carry a foreign key to `bekendmaking`. Migrations
  run on every api boot (deploy/README.md), so a seed migration holding 954 identifiers would
  raise a foreign-key violation -- and fail the boot -- on any database where the bekendmaking
  import has not run. Making the insert skip missing identifiers instead would be worse: the
  ground truth would come up silently short and every metric computed from it would be wrong
  by an unknown amount. So migration 0014 creates the structure, this script loads the data
  deliberately, and it REFUSES to load a partial frame unless told to.

Usage:
    etl/ingest/noise_groundtruth.py            # all-or-nothing
    etl/ingest/noise_groundtruth.py --partial  # load what matches, print what is missing
"""
import argparse
import asyncio
import csv
import pathlib
import sys

import asyncpg

# Through api.main.db rather than os.getenv, so this script reads .env the way every other
# script in the repo does. Reading the environment directly made it work only in a shell that
# happened to export DATABASE_URL, which is not how the rest of the project is run.
from api.main.db import DATABASE_URL

NOTES = pathlib.Path(__file__).resolve().parents[2] / "docs" / "pg-notes"


def _rows(name: str) -> list[dict]:
    with (NOTES / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--partial", action="store_true",
                   help="load the rows whose publication exists instead of refusing")
    args = p.parse_args()

    dsn = DATABASE_URL
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")

    frames = _rows("noise-frame.csv")
    members = _rows("noise-frame-member.csv")
    labels = _rows("noise-labels.csv")

    conn = await asyncpg.connect(dsn)
    try:
        wanted = {r["identifier"] for r in members} | {r["identifier"] for r in labels}
        present = {r["identifier"] for r in await conn.fetch(
            "SELECT identifier FROM bekendmaking WHERE identifier = ANY($1::text[])",
            list(wanted))}
        missing = wanted - present
        if missing:
            print(f"{len(missing)} of {len(wanted)} publication(s) are not in this database, "
                  f"e.g. {sorted(missing)[:3]}")
            if not args.partial:
                print("refusing to load a partial ground truth; run the bekendmaking import "
                      "first, or pass --partial if you accept an incomplete frame")
                return 1

        async with conn.transaction():
            await conn.executemany(
                "INSERT INTO noise_frame (name, lng, lat, radius_m, note) "
                "VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (name) DO UPDATE SET lng = EXCLUDED.lng, lat = EXCLUDED.lat, "
                "radius_m = EXCLUDED.radius_m, note = EXCLUDED.note",
                # asyncpg infers each parameter's type from the prepared statement and binds
                # strictly, so a `::float8` cast in the SQL does not make a str acceptable.
                [(r["name"], float(r["lng"]), float(r["lat"]), int(r["radius_m"]), r["note"])
                 for r in frames])
            await conn.executemany(
                "INSERT INTO noise_frame_member (frame, identifier) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                [(r["frame"], r["identifier"]) for r in members
                 if r["identifier"] in present])
            await conn.executemany(
                "INSERT INTO noise_label (identifier, label, basis, evidence, labelled_by) "
                "VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (identifier) DO UPDATE SET label = EXCLUDED.label, "
                "basis = EXCLUDED.basis, evidence = EXCLUDED.evidence, "
                "labelled_by = EXCLUDED.labelled_by",
                [(r["identifier"], r["label"], r["basis"], r["evidence"] or None,
                  r["labelled_by"]) for r in labels if r["identifier"] in present])

        # Exhaustiveness is the property the whole benchmark rests on, so report it rather
        # than assume it: every frame member must carry a label.
        gap = await conn.fetchval("SELECT count(*) FROM noise_frame_unlabelled")
        dist = await conn.fetch(
            "SELECT m.frame, nl.label, count(*) AS n FROM noise_frame_member m "
            "JOIN noise_label nl USING (identifier) GROUP BY 1, 2 ORDER BY 1, 3 DESC")
        for r in dist:
            print(f"  {r['frame']:<20} {r['label']:<12} {r['n']}")
        print(f"unlabelled frame members: {gap}")
        return 0 if gap == 0 else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
