"""
Spatial primitives: isochrone and POI-inside-a-shape.

Both /isochrone and /pois route through here because /pois needs the isochrone
polygon, and duplicating the ORS call plus the cache-key format in two routers is
exactly how the two keys drifted apart (iso: at 5 decimals, pois: at 3) and made
the shared read impossible.

POI answers come from the local `poi` table (stage 1 of the Postgres plan) and are
filtered by the *actual* isochrone polygon in Postgres. The Overpass path remains
behind `db.enabled()`; it asks for a circle of `minutes * SPEED_MAP[profile]`
metres, which is both the wrong shape and node-only — 54% of the POI in the table
are OSM ways, which `node[...]` can never return.
"""
from __future__ import annotations

import asyncio
import json

from ... import db
from ...cache import geo_cache_key, kv_get, kv_set
from ...config import SPEED_MAP
from ...logger import get_logger
from ...upstream.ors import fetch_isochrone
from ...upstream.overpass import fetch_pois

logger = get_logger("domain.geo")

ISO_TTL = 86400
POIS_TTL = 21600

# The exact set the node-only Overpass query asked for, so the default response is
# unchanged and the old-vs-new comparison stays like-for-like. Widening the default
# to all 37 categories would also put ~33k car_parking markers in the DOM.
DEFAULT_CATEGORIES = ("supermarket", "convenience", "gym")

# An unselective category in a driving isochrone can match tens of thousands of rows.
MAX_POIS = 2000

# The shape of these two queries is not cosmetic. `category = ANY($2::text[])` —
# and the identical `category IN (a, b, c)` — is a ScalarArrayOpExpr, and the
# btree_gist opclass cannot take one as an index condition, so category degrades to
# a heap Filter and the whole geom subtree is scanned:
#
#   = ANY(array)                 Index Cond: geom @ box only
#                                Rows Removed by Filter: 2337   Buffers: 1708
#   unnest + LATERAL (below)     Index Cond: category = c.category AND geom @ box
#                                Rows Removed by Filter: 8/loop Buffers: 128
#
# One plain `=` per category is the only form btree_gist pushes down, so the array
# is unnested and joined laterally. The inner LIMIT is load-bearing: without it the
# planner flattens the LATERAL back into a join filter and the 1708 buffers return.
# `AS MATERIALIZED` on a CTE does *not* achieve this — measured, it does nothing here.
# See docs/pg-notes/stage1-poi-import.md.
#
# ST_MakeValid is not defensive clutter. The polygon is whatever ORS computed, and an
# isochrone is a concave hull — a self-intersecting ring is a shape ORS can produce.
# ST_GeomFromGeoJSON accepts one without complaint and ST_Contains then returns an
# *undefined* answer rather than an error: a bowtie ring over this data returned 28
# rows with only a `NOTICE: Self-intersection`. A silently wrong count is worse than
# a 500. ST_CollectionExtract(..., 3) is needed because MakeValid on a bad ring yields
# a MultiPolygon or a GeometryCollection.
_POI_IN_SHAPE = """
WITH shape AS (
    SELECT ST_CollectionExtract(
               ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON($1), 4326)), 3) AS g
)
SELECT p.osm_type, p.osm_id, p.category, p.name, p.tags, p.lon, p.lat
FROM unnest($2::text[]) AS c(category), shape
CROSS JOIN LATERAL (
    SELECT osm_type, osm_id, category, name, tags,
           ST_X(geom) AS lon, ST_Y(geom) AS lat
    FROM poi
    WHERE deleted_at IS NULL
      AND category = c.category
      AND ST_Contains(shape.g, geom)
    LIMIT $3
) p
LIMIT $3
"""

# Degraded shape when no isochrone is available (no ORS key, ORS down). Still a
# Postgres answer, not an Overpass one — a circle from the table beats a circle
# from a node-only upstream.
#
# The radius is materialised as a geodesic buffer *polygon* rather than written as
# ST_DWithin(geom::geography, pt::geography, $r), because the cast to geography
# leaves the geometry GiST unusable for the distance term: measured, that form gave
#   Index Cond: category = c.category only
#   Rows Removed by Filter: 533/loop   Buffers: 1445
# against 1/loop and 100 buffers for the buffer-polygon form, same 42 rows.
_POI_IN_CIRCLE = """
WITH shape AS (
    SELECT ST_Buffer(ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography, $4)::geometry AS g
)
SELECT p.osm_type, p.osm_id, p.category, p.name, p.tags, p.lon, p.lat
FROM unnest($3::text[]) AS c(category), shape
CROSS JOIN LATERAL (
    SELECT osm_type, osm_id, category, name, tags,
           ST_X(geom) AS lon, ST_Y(geom) AS lat
    FROM poi
    WHERE deleted_at IS NULL
      AND category = c.category
      AND ST_Contains(shape.g, geom)
    LIMIT $5
) p
LIMIT $5
"""


# Single-flight. The browser fires /isochrone and /pois concurrently for the same
# (lng, lat, minutes, profile), and /pois needs the same polygon, so the cache alone
# cannot deduplicate them: both miss before either writes. Measured 38 ORS calls for
# 18 unique parameter sets (2.1x) — and the ORS free tier is 50 per day per endpoint,
# so the amplifier, not the usage, is what exhausts the quota.
#
# Keyed on the cache key, so it agrees with the cache by construction. Both awaits are
# shielded: without the shield, a client disconnect cancels the awaiting coroutine,
# which propagates into the shared task and fails the *other* request too.
_iso_inflight: dict[str, asyncio.Task] = {}


async def _fetch_isochrone_cached(key: str, lng: float, lat: float, minutes: int, profile: str) -> dict:
    data = await fetch_isochrone(lng, lat, minutes, profile)
    await kv_set(key, data, ttl_seconds=ISO_TTL)
    return data


async def get_isochrone(lng: float, lat: float, minutes: int, profile: str) -> dict:
    key = geo_cache_key("iso", lng, lat, minutes, profile)
    if cached := await kv_get(key):
        logger.info("iso cache hit", extra={"cache_key": key})
        return cached

    if existing := _iso_inflight.get(key):
        logger.info("iso in flight, joining", extra={"cache_key": key})
        return await asyncio.shield(existing)

    task = asyncio.create_task(_fetch_isochrone_cached(key, lng, lat, minutes, profile))
    _iso_inflight[key] = task
    try:
        return await asyncio.shield(task)
    finally:
        _iso_inflight.pop(key, None)


# poi.osm_type is char(1) to keep the table narrow; Overpass spells it out, and the
# response contract is Overpass's, so the two paths must not disagree on it.
_OSM_TYPE = {"n": "node", "w": "way", "r": "relation"}


def _element(row) -> dict:
    """Overpass element shape, so PoiMarker/PoiPopup read `tags` either way."""
    return {
        "type": _OSM_TYPE[row["osm_type"]],
        "id": row["osm_id"],
        "lon": row["lon"],
        "lat": row["lat"],
        "category": row["category"],
        "tags": row["tags"],
    }


async def get_pois(
    lng: float, lat: float, minutes: int, profile: str, categories: list[str] | None = None
) -> dict:
    cats = list(categories) if categories else list(DEFAULT_CATEGORIES)

    if not db.enabled():
        # Pre-Postgres path. Cached, because every call is a 3-mirror Overpass query.
        key = geo_cache_key("pois", lng, lat, minutes, profile)
        if cached := await kv_get(key):
            logger.info("pois cache hit", extra={"cache_key": key})
            return cached
        data = await fetch_pois(lng, lat, minutes, profile)
        await kv_set(key, data, ttl_seconds=POIS_TTL)
        return data

    # Deliberately not cached: the query is a single index scan on a local table, and
    # a cached copy would only go stale after the next import_poi.py refresh.
    polygon = None
    try:
        iso = await get_isochrone(lng, lat, minutes, profile)
        features = iso.get("features") or []
        if features:
            polygon = features[0].get("geometry")
    except Exception as e:
        logger.warning("iso unavailable, falling back to a circle", extra={"error": str(e)})

    async with db.acquire() as conn:
        if polygon is not None:
            # ST_GeomFromGeoJSON yields SRID 0 unless the GeoJSON carries a crs member.
            rows = await conn.fetch(_POI_IN_SHAPE, json.dumps(polygon), cats, MAX_POIS)
            shape = "isochrone"
        else:
            radius = minutes * SPEED_MAP.get(profile, 80)
            rows = await conn.fetch(_POI_IN_CIRCLE, lng, lat, cats, radius, MAX_POIS)
            shape = "circle"

    logger.info("← pois", extra={"source": "postgres", "shape": shape, "elements": len(rows)})
    if len(rows) == MAX_POIS:
        logger.warning("pois truncated", extra={"limit": MAX_POIS, "categories": cats})
    return {"elements": [_element(r) for r in rows]}
