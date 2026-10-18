"""
Import OSM POI from a Geofabrik `.osm.pbf` extract into the `poi` table.

Usage:
    python scripts/import_poi.py data/noord-holland-latest.osm.pbf

Replaces the per-request Overpass call in `api/main/upstream/overpass.py`. See
`docs/postgres-refactor-plan.md` "阶段 1 Spec" for why this moves in-house.

The tag whitelist is **not** written here. It is read from `poi_category_rule`, so that
table is the single source of truth both for what gets imported and for how it is
categorised; widening the import is an INSERT, not a code change.

Scope: the run is named after the extract (`osm_poi:noord-holland`), and the
soft-delete pass is confined to the bounding box of the rows the run actually staged.
Importing one province therefore cannot mass-delete the rest of the country.

Known limitation: relations are skipped. A POI mapped as a multipolygon relation (a
handful of large parks and hospital grounds) is missed. Picking them up needs
pyosmium's area handling, which is a second pass over the file; deferred until the
superset check in §1.7 shows it matters.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg
import osmium
import osmium.filter
import osmium.geom
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Rows per COPY. Large enough that round-trips are irrelevant, small enough that the
# batch and its JSON stay well inside memory on a full-country run.
BATCH_SIZE = 50_000

# A staged count below this fraction of the previous successful run is treated as a
# broken extract rather than as real-world disappearance. Recording a truncated
# download as a mass delete would permanently poison the table.
SENTINEL_MIN_RATIO = 0.5

_CLAIM_RUN = """
INSERT INTO etl_run (source, note) VALUES ($1, $2) RETURNING id
"""

_LAST_GOOD = """
SELECT row_count FROM etl_run
WHERE source = $1 AND status = 'succeeded' AND row_count IS NOT NULL
ORDER BY started_at DESC
LIMIT 1
"""

# Interpretation of the staged bytes happens here, in SQL, rather than in Python:
# SRID assignment, reduction to a point, and category assignment are all things we want
# to be able to read and test as queries.
_MERGE = """
INSERT INTO poi (osm_type, osm_id, category, name, geom, tags, first_seen_run, last_changed_run)
SELECT i.osm_type, i.osm_id, rule.category, nullif(i.tags->>'name', ''), red.pt, i.tags, $1, $1
FROM poi_import i
CROSS JOIN LATERAL (
    SELECT ST_SetSRID(ST_GeomFromWKB(decode(i.geom_wkb, 'hex')), 4326) AS g
) raw
CROSS JOIN LATERAL (
    SELECT CASE
        WHEN ST_GeometryType(raw.g) = 'ST_Point' THEN raw.g
        -- A closed way is an outline: a supermarket drawn as its building. Reduce it
        -- with ST_PointOnSurface and never ST_Centroid -- the centroid of a C- or
        -- ring-shaped polygon can fall outside the polygon itself.
        --
        -- ST_MakeValid is needed because OSM outlines self-intersect often enough to
        -- matter at country scale, and it can return a collection, hence
        -- ST_CollectionExtract. If nothing survives, the row is dropped by the WHERE
        -- below instead of failing the whole import.
        WHEN ST_IsClosed(raw.g) AND ST_NPoints(raw.g) >= 4 THEN (
            SELECT CASE WHEN ST_IsEmpty(poly) THEN NULL ELSE ST_PointOnSurface(poly) END
            FROM ST_CollectionExtract(ST_MakeValid(ST_MakePolygon(raw.g)), 3) AS poly
        )
        -- An open way (a bus stop mapped on a platform line) has no interior.
        ELSE ST_PointOnSurface(raw.g)
    END AS pt
) red
CROSS JOIN LATERAL (
    -- An object can carry several matching tags; lowest priority wins. The secondary
    -- sort on category is only there to make ties deterministic, so that a re-import
    -- of unchanged data produces no diff.
    SELECT r.category
    FROM poi_category_rule r
    WHERE i.tags ? r.osm_key AND i.tags->>r.osm_key = r.osm_value
    ORDER BY r.priority, r.category
    LIMIT 1
) rule
WHERE red.pt IS NOT NULL
ON CONFLICT (osm_type, osm_id) DO UPDATE SET
    category         = EXCLUDED.category,
    name             = EXCLUDED.name,
    geom             = EXCLUDED.geom,
    tags             = EXCLUDED.tags,
    last_changed_run = EXCLUDED.last_changed_run,
    -- Resurrect: an object that comes back must not stay soft-deleted. first_seen_run
    -- is deliberately left alone, so it keeps meaning "when we first saw this".
    deleted_at       = NULL
-- Without this, a refresh rewrites every row it sees even when nothing changed, and
-- because last_changed_run and geom are both indexed none of those rewrites can be
-- HOT. One no-op re-import of Noord-Holland doubled the heap and the spatial index.
WHERE poi.category   IS DISTINCT FROM EXCLUDED.category
   OR poi.name       IS DISTINCT FROM EXCLUDED.name
   OR poi.geom       IS DISTINCT FROM EXCLUDED.geom
   OR poi.tags       IS DISTINCT FROM EXCLUDED.tags
   OR poi.deleted_at IS NOT NULL
"""

# Soft delete, never TRUNCATE-and-reload: /pois must keep answering during the import.
#
# Scoped to the bounding box of what this run staged, so importing one province cannot
# mass-delete the rest of the country. Anti-joined against the staging table rather
# than looking for a stale run id, which is what lets unchanged rows go untouched.
_SOFT_DELETE = """
UPDATE poi p SET deleted_at = now()
WHERE p.deleted_at IS NULL
  AND ST_Intersects(p.geom, ST_MakeEnvelope($1, $2, $3, $4, 4326))
  AND NOT EXISTS (
      SELECT 1 FROM poi_import i
      WHERE i.osm_type = p.osm_type AND i.osm_id = p.osm_id
  )
"""

_STAGED_EXTENT = """
SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e)
FROM (
    SELECT ST_Extent(ST_GeomFromWKB(decode(geom_wkb, 'hex'))) AS e FROM poi_import
) s
"""


def _source_name(path: Path) -> str:
    name = path.name
    for suffix in (".osm.pbf", ".osm.bz2", ".pbf"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return f"osm_poi:{name.removesuffix('-latest')}"


def _stream_batches(path: Path, rules: list[tuple[str, str]], stats: dict[str, int]):
    """Yield batches of (osm_type, osm_id, hex_wkb, tags_json) read straight off the pbf.

    Synchronous and blocking on purpose: this is a one-shot script, so there is nothing
    else for the event loop to do while libosmium works.
    """
    wkb = osmium.geom.WKBFactory()
    processor = (
        osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY)
        # Ways arrive as bare node references; this keeps the node coordinates so a way
        # can be turned into a geometry in the same single pass.
        .with_locations()
        .with_filter(osmium.filter.TagFilter(*rules))
    )

    batch: list[tuple[str, int, str, str]] = []
    for obj in processor:
        if obj.is_node():
            osm_type, geom = "n", wkb.create_point(obj)
        else:
            try:
                geom = wkb.create_linestring(obj)
            except (osmium.InvalidLocationError, RuntimeError):
                # A way whose nodes fall outside the extract has no usable geometry.
                stats["no_geometry"] += 1
                continue
            osm_type = "w"

        batch.append((osm_type, obj.id, geom, json.dumps({t.k: t.v for t in obj.tags})))
        if len(batch) >= BATCH_SIZE:
            yield batch
            batch = []

    if batch:
        yield batch


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pbf", type=Path, help="path to a .osm.pbf extract")
    args = parser.parse_args()

    if not args.pbf.is_file():
        raise SystemExit(f"no such file: {args.pbf}")

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")

    source = _source_name(args.pbf)
    conn = await asyncpg.connect(dsn)
    try:
        rules = [
            (r["osm_key"], r["osm_value"])
            for r in await conn.fetch("SELECT osm_key, osm_value FROM poi_category_rule")
        ]
        if not rules:
            raise SystemExit("poi_category_rule is empty — run the migrations first")
        print(f"{source}: {len(rules)} tag rules")

        # Committed on its own so the partial unique index really does exclude a second
        # concurrent run rather than just a second transaction in this process.
        try:
            run_id = await conn.fetchval(_CLAIM_RUN, source, str(args.pbf))
        except asyncpg.UniqueViolationError:
            raise SystemExit(f"an import of {source} is already running") from None
        print(f"etl_run {run_id} claimed")

        try:
            # The staging index is built after the COPY, not declared in the migration,
            # so it does not have to be maintained row by row while loading.
            await conn.execute("TRUNCATE poi_import")
            await conn.execute("DROP INDEX IF EXISTS poi_import_key")
            staged = 0
            stats = {"no_geometry": 0}
            for batch in _stream_batches(args.pbf, rules, stats):
                await conn.copy_records_to_table(
                    "poi_import",
                    records=batch,
                    columns=["osm_type", "osm_id", "geom_wkb", "tags"],
                )
                staged += len(batch)
                print(f"  staged {staged}")
            print(f"staged {staged} objects, {stats['no_geometry']} without geometry")
            await conn.execute("CREATE INDEX poi_import_key ON poi_import (osm_type, osm_id)")

            previous = await conn.fetchval(_LAST_GOOD, source)
            if previous and staged < previous * SENTINEL_MIN_RATIO:
                note = f"sentinel: staged {staged} vs {previous} last run"
                await conn.execute(
                    "UPDATE etl_run SET status = 'failed', finished_at = now(), note = $2 WHERE id = $1",
                    run_id,
                    note,
                )
                raise SystemExit(f"{note} — refusing to write a diff")

            async with conn.transaction():
                merged = await conn.execute(_MERGE, run_id)
                extent = await conn.fetchrow(_STAGED_EXTENT)
                deleted = await conn.execute(_SOFT_DELETE, *extent)
                live = await conn.fetchval("SELECT count(*) FROM poi WHERE deleted_at IS NULL")
                await conn.execute(
                    "UPDATE etl_run SET status = 'succeeded', finished_at = now(), row_count = $2 WHERE id = $1",
                    run_id,
                    staged,
                )
        except SystemExit:
            raise
        except Exception as e:
            await conn.execute(
                "UPDATE etl_run SET status = 'failed', finished_at = now(), note = $2 WHERE id = $1",
                run_id,
                f"{type(e).__name__}: {e}",
            )
            raise

        print(f"merge {merged}, soft delete {deleted}")
        print(f"poi now has {live} live rows")
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
