"""Semantic search over official publications: rank by meaning, optionally within reach.

WHY THE SIMILARITY IS COMPUTED IN POSTGRES. An isochrone over a year matches a few thousand
publications. Shipping their 768-float vectors to Python to take a dot product would be
megabytes per request for arithmetic the database can do where the data already is. That, not
the index, is what pgvector buys here.

TWO MODES, AND THEY ARE DIFFERENT PROBLEMS.
  * within an isochrone — the spatial predicate runs FIRST and already cuts the corpus to a
    few thousand rows, so the ranking is an exact scan over a small set. No ANN index is
    needed and the planner is unlikely to choose one.
  * over the whole corpus — 94,183 rows with no filter, which is the case an HNSW index
    exists for.
Both are offered because the comparison is the only way to find out whether the index earns
its place in THIS workload rather than in general. See docs/pg-notes/semantic-search.md.

`<=>` is cosine DISTANCE: 0 identical, 1 orthogonal, 2 opposite. The API returns
`similarity = 1 - distance` because a number that grows with relevance is what a caller
expects, and silently handing back a distance where a score is expected inverts every ranking
downstream without erroring.
"""
from __future__ import annotations

import json
from datetime import date

from ... import db
from ...config import SPEED_MAP
from ...logger import get_logger
from ..geo.service import get_isochrone
from . import encoder

logger = get_logger("domain.search")

MAX_LIMIT = 50

# The vector is passed as a parameter, never interpolated: it is 768 floats and the planner
# caches one query text per shape. `plan_cache_mode=force_custom_plan` (set in db.py, and
# load-bearing for the spatial parameter) keeps that from becoming a generic plan.
# RANK FIRST, THEN DECORATE. The ordering of these two steps is worth a paragraph because
# getting it wrong cost 4.5 s a query and the EXPLAIN named the culprit exactly.
#
# `bekendmaking_activity` is a VIEW: `activity_text` is reassembled by six regexes over
# `body_text` every time a row is read. Joining it alongside the ranking meant running those
# regexes over every candidate — 45,000 bodies — to produce a snippet for the twenty rows that
# survive. The vector sort itself measured 787 ms; the view was the rest.
#
# So the ranking touches only `bekendmaking_embedding` and the cheap filter columns, takes its
# LIMIT, and the expensive view and the body/noise joins run against the handful of rows left.
_RANK_CORE = """
ranked AS (
    SELECT e.identifier, e.embedding <=> $1::vector AS distance
    FROM bekendmaking_embedding e
    JOIN bekendmaking b USING (identifier)
    {join}
    WHERE {where}
    ORDER BY distance
    LIMIT {limit}
)
"""

# Decoration, over the LIMITed set only.
_DECORATE = """
SELECT r.identifier, r.distance, b.title, b.doc_type, b.published_at, b.activiteit, b.url,
       coalesce(b.gemeente, b.authority) AS gemeente,
       a.activity_text,
       bb.start_date AS event_start, bb.end_date AS event_end,
       bb.status = 'ok_derived' AS date_derived,
       n.score AS noise_score,
       CASE WHEN n.is_noisy IS NULL THEN 'unscored'
            WHEN n.is_noisy THEN 'noisy' ELSE 'quiet' END AS noise,
       ST_X({point}) AS lon, ST_Y({point}) AS lat
FROM ranked r
JOIN bekendmaking b USING (identifier)
JOIN bekendmaking_activity a USING (identifier)
LEFT JOIN bekendmaking_body bb USING (identifier)
LEFT JOIN bekendmaking_noise_rank n USING (identifier)
ORDER BY r.distance
"""

# The representative point differs by mode and the difference is not cosmetic. Within a shape
# it is the geometry that actually intersected — a plot straddling the isochrone edge should be
# marked where it reaches in. Over the whole corpus there is no shape to intersect, so it is the
# publication's largest geometry, chosen the way `DISTINCT ON` picks one in domains/bekendmaking.
_ANY_POINT = """(SELECT ST_PointOnSurface(l.geom) FROM bekendmaking_locatie l
     WHERE l.identifier = b.identifier
     ORDER BY (GeometryType(l.geom) <> 'POINT'), ST_Area(l.geom) DESC LIMIT 1)"""

_SPATIAL_CTE = """
WITH shape AS (SELECT {shape_expr} AS g),
hit AS (
    SELECT DISTINCT ON (l.identifier) l.identifier, ST_PointOnSurface(l.geom) AS pt
    FROM bekendmaking_locatie l, shape
    WHERE l.published_at >= $2 AND l.published_at <= $3
      AND ST_Intersects(shape.g, l.geom)
    ORDER BY l.identifier, (GeometryType(l.geom) <> 'POINT'), ST_Area(l.geom) DESC
),
"""

_WHERE_COMMON = ("b.published_at >= $2 AND b.published_at <= $3 "
                 "AND ($4::text[] IS NULL OR b.doc_type = ANY($4::text[]))")

# Same reasoning as domains/bekendmaking: a self-intersecting ORS ring makes ST_Intersects
# undefined rather than refused, and the geography cast would make the GiST unusable.
_SHAPE_FROM_GEOJSON = (
    "ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON($5), 4326)), 3)")
_SHAPE_FROM_RADIUS = (
    "ST_Buffer(ST_SetSRID(ST_MakePoint($5, $6), 4326)::geography, $7)::geometry")


def _sql(spatial: bool, shape_expr: str, limit: int) -> str:
    if spatial:
        return (_SPATIAL_CTE.format(shape_expr=shape_expr)
                + _RANK_CORE.format(join="JOIN hit USING (identifier)",
                                    where=_WHERE_COMMON, limit=limit).lstrip()
                + _DECORATE.format(point="(SELECT pt FROM hit WHERE hit.identifier = r.identifier)"))
    return ("WITH " + _RANK_CORE.format(join="", where=_WHERE_COMMON, limit=limit).lstrip()
            + _DECORATE.format(point=_ANY_POINT))


async def _shape(lng: float, lat: float, minutes: int, profile: str):
    try:
        iso = await get_isochrone(lng, lat, minutes, profile)
        features = iso.get("features") or []
        if features:
            return features[0].get("geometry")
    except Exception as e:
        logger.warning("iso unavailable, falling back to a circle", extra={"error": str(e)})
    return None


async def search(
    q: str,
    date_from: date,
    date_to: date,
    lng: float | None = None,
    lat: float | None = None,
    minutes: int = 10,
    profile: str = "walking",
    doc_type: list[str] | None = None,
    limit: int = 20,
) -> dict:
    empty = {"query": q, "items": [], "total": 0,
             "window": {"from": date_from.isoformat(), "to": date_to.isoformat()}}
    if not db.enabled():
        return {**empty, "shape": "unavailable", "reason": "postgres not configured"}
    if not encoder.available():
        # 503-worthy, and said in the body rather than returned as zero results: an
        # unavailable encoder and a query that matches nothing look identical otherwise.
        return {**empty, "shape": "unavailable", "reason": encoder.reason()}

    vec = encoder.encode(q)
    if vec is None:
        return {**empty, "shape": "unavailable", "reason": encoder.reason()}
    qvec = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    limit = max(1, min(limit, MAX_LIMIT))

    if lng is None or lat is None:
        shape = "corpus"
        sql, args = _sql(False, "", limit), [qvec, date_from, date_to, doc_type]
    else:
        polygon = await _shape(lng, lat, minutes, profile)
        if polygon is not None:
            shape, geom, expr = "isochrone", [json.dumps(polygon)], _SHAPE_FROM_GEOJSON
        else:
            shape = "circle"
            geom = [lng, lat, minutes * SPEED_MAP.get(profile, 80)]
            expr = _SHAPE_FROM_RADIUS
        sql, args = _sql(True, expr, limit), [qvec, date_from, date_to, doc_type, *geom]

    async with db.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = [{
        "identifier": r["identifier"], "title": r["title"], "doc_type": r["doc_type"],
        "published_at": r["published_at"].isoformat(), "activiteit": list(r["activiteit"] or []),
        "gemeente": r["gemeente"], "url": r["url"],
        "snippet": (r["activity_text"] or "")[:240],
        "event_start": r["event_start"].isoformat() if r["event_start"] else None,
        "event_end": r["event_end"].isoformat() if r["event_end"] else None,
        "date_derived": r["date_derived"],
        "noise": r["noise"],
        "noise_score": round(float(r["noise_score"]), 3) if r["noise_score"] is not None else None,
        "similarity": round(1.0 - float(r["distance"]), 4),
        # NULL where a publication has no geometry at all (0.12% of the corpus). The caller
        # gets the row with a null position rather than losing it: a result that cannot be
        # mapped is still a result, and dropping it would make the list disagree with `total`.
        "lon": r["lon"], "lat": r["lat"],
    } for r in rows]

    logger.info("← search", extra={"shape": shape, "q": q, "returned": len(items),
                                        "top": items[0]["similarity"] if items else None})
    return {**empty, "shape": shape, "total": len(items), "items": items}
