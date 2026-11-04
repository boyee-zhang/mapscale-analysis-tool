"""
Official Dutch government announcements, answered for a place and a time window.

The data is the `bekendmaking` / `bekendmaking_locatie` pair loaded by
`scripts/import_bekendmaking.py` from KOOP's SRU repository. This domain only
reads it; the ETL is a script, not a request path.

Shape comes from `domains/geo`: the same isochrone `/pois` uses, so "what is
within 10 minutes" means the same thing for a supermarket and for a building
permit. There is no Overpass-style fallback here — without Postgres there is no
data at all, so the endpoint reports that rather than inventing a degraded answer.
"""
from __future__ import annotations

import json
from datetime import date

from ... import db
from ...config import SPEED_MAP
from ...logger import get_logger
from ..geo.service import get_isochrone

logger = get_logger("domain.bekendmaking")

# A 24 km shape over a year matches 54,498 publications and 4.8 MB of titles alone
# (measured 2026-10-29), so the list is capped and the histogram is computed
# separately. Returning a truncated list with a histogram derived *from that list*
# would under-report every month past the cap.
MAX_ITEMS = 500

# What "建设许可" / a building permit is in this dataset, and why it takes two fields:
# `doc_type` alone ('omgevingsvergunning', 13,142 rows) also covers felling trees,
# signage and zoning; `activiteit` narrows it to the 10,342 that involve building.
# `activiteit` is only ever populated for omgevingsvergunning — every other doc_type
# has an empty array — so an activiteit filter implies that type anyway. Both are
# passed explicitly so the filter reads as what it means.
BUILDING_PERMIT = {"doc_type": ["omgevingsvergunning"], "activiteit": ["bouwen"]}

# `stage` is DERIVED FROM THE TITLE, not a field the source provides. It is the one piece
# of "what happened here" that matters most for a building permit — an application is a
# plan, a decision is a thing that will be built — and the SRU record has no field for it:
# `raw` carries only subject/jaargang/publisher/timestamps. So it is a keyword match on
# `title`, and it is labelled as derived everywhere it surfaces.
#
# MEASURED over all 10,342 building permits: besluit 5,139 (49.9%), aanvraag 5,128 (49.8%),
# ontwerp 18, overig 21 (0.2%). Three things make it safe enough to show:
#   - 'besluit' is tested before 'aanvraag' because Dutch titles read "Besluit op de
#     aanvraag …", so a title carrying both words is a decision. Only 25 of the 10,342
#     carry both, so the precedence is nearly inert either way.
#   - the match is unanchored. Anchoring it at ^ cost 147 rows (1.4%), all of them Diemen,
#     which prefixes every title with "Gemeente Diemen: ".
#   - what is left in `overig` is real ambiguity, not a parser gap: bare
#     "Omgevingsvergunning" and "Geen vergunning nodig - …".
# It is computed in the query rather than stored because the next province may phrase
# titles differently, and a stored column would have to be backfilled to find that out.
#
# The spatial predicate is ST_Intersects, NOT the ST_Contains that `/pois` uses.
# That is not a style difference: `poi.geom` is always a POINT, where the two
# predicates agree, while `bekendmaking_locatie.geom` is 16% POLYGON and 13%
# LINESTRING. A permit whose plot straddles the isochrone edge is within reach,
# and ST_Contains would drop it.
#
# DISTINCT ON collapses the fan-out: a publication carries 2.5 geometries on
# average (the dominant pattern is a POINT plus the POLYGON of the same plot),
# and 9,213 of the 10,342 building permits have exactly that pair. Without the
# collapse the map draws two or three markers on one address, which is the same
# fan-out that made an earlier `count(*)` read 282 for 119 publications.
#
# The representative point is chosen among the geometries that intersect the
# shape, not among all of a publication's geometries. That is deliberate: if a
# permit's point sits outside the isochrone while its plot polygon reaches in,
# the marker lands on the part that is actually within reach. It also keeps the
# whole thing in one index pass — resolving the point with a second lookup keyed
# on identifier measured 414 ms against 30 ms for this form (same 800 m shape, same
# 370 rows), because it becomes one bitmap scan per surviving publication: 370
# loops, 1,518 buffers, 101 ms of it pure I/O.
#
# `matched` spells its columns out instead of `b.*` to state the contract, and for no
# other reason — I wrote `b.*` first and predicted that dragging `raw jsonb` along would
# dominate, because the CTE is referenced four times and therefore materialised. MEASURED
# 2026-10-29 at the widest shape: `b.*` 1007/896 ms, the column list 879/902 ms. No
# difference. The spill (7,294 temp blocks at work_mem=4MB, read back four times) is driven
# by the 54,414 rows, not by their width, and `raw` is only a few hundred bytes per row.
# Quoted here because the prediction was wrong, not because the rewrite paid.
#
# Filters are `$n::text[] IS NULL OR col = ANY(...)` rather than being assembled
# into the SQL string, so there is exactly one query text for the planner to
# cache and no interpolation.
_HIT_CTE = """
WITH shape AS (
    SELECT {shape_expr} AS g
),
hit AS (
    SELECT DISTINCT ON (l.identifier) l.identifier, ST_PointOnSurface(l.geom) AS pt
    FROM bekendmaking_locatie l, shape
    WHERE l.published_at >= $1 AND l.published_at <= $2
      AND ST_Intersects(shape.g, l.geom)
    ORDER BY l.identifier, (GeometryType(l.geom) <> 'POINT'), ST_Area(l.geom) DESC
),
scored AS (
    SELECT b.identifier, b.title, b.doc_type, b.published_at, b.activiteit,
           b.url, b.gemeente, b.authority, hit.pt,
           CASE
               WHEN b.title ~* 'ontwerp(besluit|omgevingsvergunning)|voornemen' THEN 'ontwerp'
               WHEN b.title ~* 'besluit|verleend|toegekend|geweigerd|ingetrokken'
                    OR b.title ~* 'toestemming|geen vergunning nodig' THEN 'besluit'
               WHEN b.title ~* 'aanvraag|aanvragen|ingekomen' THEN 'aanvraag'
               ELSE 'overig'
           END AS stage,
           n.score AS noise_score, n.is_noisy, n.model_version,
           -- Three buckets, and `unscored` is a bucket of its own rather than folded into
           -- `quiet`. A publication imported after the last scoring run has no score, and
           -- "we have not judged this" must not be served as "this is quiet" -- the same
           -- rule this endpoint already follows for `shape: "unavailable"` against `total: 0`.
           CASE WHEN n.is_noisy IS NULL THEN 'unscored'
                WHEN n.is_noisy THEN 'noisy' ELSE 'quiet' END AS noise
    FROM hit JOIN bekendmaking b USING (identifier)
    -- LEFT, not INNER: an unscored publication must still appear on the map. An inner join
    -- here would silently shrink `total` after every import until the scorer is re-run, which
    -- is the worst version of this bug -- the count would look plausible.
    LEFT JOIN bekendmaking_noise_rank n ON n.identifier = b.identifier
    WHERE ($3::text[] IS NULL OR b.doc_type = ANY($3::text[]))
      AND ($4::text[] IS NULL OR b.activiteit && $4::text[])
),
-- The noise filter is applied one level out because a CASE alias cannot be referenced from
-- the WHERE of the same SELECT, and repeating the expression is how the two drift apart.
-- Everything downstream reads `matched`, so `total` and the histogram count the filtered set,
-- which is what makes the facets agree with the list.
matched AS (
    SELECT * FROM scored
    WHERE ($5::text[] IS NULL OR noise = ANY($5::text[]))
)
"""

# ST_MakeValid + CollectionExtract for the same reason as in domains/geo: an
# isochrone is a concave hull and ORS can emit a self-intersecting ring, which
# ST_Intersects answers *undefined* rather than refusing.
_SHAPE_FROM_GEOJSON = (
    "ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON($6), 4326)), 3)"
)
# Geodesic buffer polygon rather than ST_DWithin(geom::geography, …): the cast
# leaves the geometry GiST unusable, measured in docs/pg-notes/stage1-poi-import.md.
_SHAPE_FROM_RADIUS = (
    "ST_Buffer(ST_SetSRID(ST_MakePoint($6, $7), 4326)::geography, $8)::geometry"
)

# `sort` exists because of the item cap, not as a convenience. `items` is capped at 500 and
# `ORDER BY published_at DESC` fills that cap with the most RECENT publications -- so a caller
# asking "what is loudest around here" would get the 500 newest and a noise column, which is a
# different question answered in the same shape. Ordering by score makes the cap select the
# rows the caller asked for. NULLS LAST on either ordering keeps unscored rows from taking the
# top slots on a question they cannot answer.
_ORDERS = {
    "recent": "published_at DESC, identifier",
    "noise": "noise_score DESC NULLS LAST, published_at DESC, identifier",
}

# One query, not two. The facets and the item list both need `matched`, and as separate
# statements each parsed the ORS polygon and walked the GiST index again. The merge is
# justified structurally — one spatial pass and one round trip instead of two — and NOT by
# the 1.36 s + 2.39 s I first measured for the split form: those readings were taken while
# two of my own jobs were loading the same instance, and they are not usable. Clean
# end-to-end figures for the merged form, nothing else running: 0.10-0.71 s for a 10-minute
# walk, 1.73-2.50 s for the widest shape this endpoint allows.
#
# The month series is what answers "from when to when" and must count every match, not
# only the ones that survived the item cap, so it cannot be derived from `items`.
#
# NOTE for any caller that draws `by_activiteit`: these counts do NOT sum to `total`.
# A permit can carry several activities (284 bouwen permits in central Amsterdam also
# carry 12 ruimtelijke ordening, 1 reclame, 1 slopen), so this is a per-activity tally,
# not a partition — correct as a bar chart, wrong as a pie.
_FACETS_AND_ITEMS = """
SELECT
    (SELECT count(*) FROM matched) AS total,
    (SELECT coalesce(jsonb_agg(m ORDER BY m->>'month'), '[]'::jsonb) FROM (
        SELECT jsonb_build_object(
                   'month', to_char(published_at, 'YYYY-MM'),
                   'count', count(*)) AS m
        FROM matched GROUP BY to_char(published_at, 'YYYY-MM')) s) AS months,
    (SELECT coalesce(jsonb_agg(t ORDER BY (t->>'count')::int DESC), '[]'::jsonb) FROM (
        SELECT jsonb_build_object('doc_type', doc_type, 'count', count(*)) AS t
        FROM matched GROUP BY doc_type) s) AS by_type,
    (SELECT coalesce(jsonb_agg(a ORDER BY (a->>'count')::int DESC), '[]'::jsonb) FROM (
        SELECT jsonb_build_object('activiteit', act, 'count', count(*)) AS a
        FROM matched, unnest(activiteit) act GROUP BY act) s) AS by_activiteit,
    (SELECT coalesce(jsonb_agg(g ORDER BY (g->>'count')::int DESC), '[]'::jsonb) FROM (
        SELECT jsonb_build_object('stage', stage, 'count', count(*)) AS g
        FROM matched GROUP BY stage) s) AS by_stage,
    -- Unlike `by_activiteit`, this one IS a partition: every matched row lands in exactly one
    -- of noisy / quiet / unscored, so these counts sum to `total` and a pie chart is correct.
    (SELECT coalesce(jsonb_agg(n ORDER BY (n->>'count')::int DESC), '[]'::jsonb) FROM (
        SELECT jsonb_build_object('noise', noise, 'count', count(*)) AS n
        FROM matched GROUP BY noise) s) AS by_noise,
    -- The model behind the badges, so a caller can state how much to trust them instead of
    -- presenting a 0.891 F1 classifier as ground truth. A list rather than one object because
    -- a partial rescore leaves two versions in play, and hiding that would make a mixed
    -- population look uniform; one element is the normal case.
    (SELECT coalesce(jsonb_agg(jsonb_build_object(
                'version', m.version, 'embed_model', m.embed_model,
                'threshold', m.threshold, 'threshold_basis', m.threshold_basis,
                'trained_at', m.trained_at, 'metrics', m.metrics)), '[]'::jsonb)
     FROM noise_model m
     WHERE m.version IN (SELECT model_version FROM matched
                         WHERE model_version IS NOT NULL)) AS noise_models,
    (SELECT coalesce(jsonb_agg(i), '[]'::jsonb) FROM (
        SELECT jsonb_build_object(
                   'identifier', identifier,
                   'title', title,
                   'doc_type', doc_type,
                   'published_at', published_at,
                   'activiteit', activiteit,
                   'stage', stage,
                   'gemeente', coalesce(gemeente, authority),
                   'url', url,
                   'noise', noise,
                   -- Rounded, and sent alongside the verdict rather than instead of it: the
                   -- boolean is the product decision and the number is why, so a UI can sort
                   -- by confidence and still show one badge. NULL where `noise` is 'unscored'.
                   'noise_score', round(noise_score::numeric, 3),
                   'lon', ST_X(pt),
                   'lat', ST_Y(pt)) AS i
        FROM matched ORDER BY {order} LIMIT {limit}) s) AS items
"""


async def _shape(lng: float, lat: float, minutes: int, profile: str):
    """The isochrone polygon, or None if ORS could not supply one."""
    try:
        iso = await get_isochrone(lng, lat, minutes, profile)
        features = iso.get("features") or []
        if features:
            return features[0].get("geometry")
    except Exception as e:
        logger.warning("iso unavailable, falling back to a circle", extra={"error": str(e)})
    return None


async def get_bekendmakingen(
    lng: float,
    lat: float,
    date_from: date,
    date_to: date,
    minutes: int = 10,
    profile: str = "walking",
    doc_type: list[str] | None = None,
    activiteit: list[str] | None = None,
    noise: list[str] | None = None,
    sort: str = "recent",
) -> dict:
    """
    Announcements whose geometry meets the (lng, lat, minutes, profile) isochrone and
    whose publication date is inside [date_from, date_to].

    `None` for either filter means "no restriction"; an explicitly empty list means
    "nothing matches" and is answered without touching the database. Conflating the
    two is the bug that made an empty POI selection return the default three
    categories — same trap, so it is handled the same way here.
    """
    empty = {
        "total": 0, "months": [], "by_type": [], "by_activiteit": [], "by_stage": [],
        "by_noise": [], "noise_models": [], "items": [], "truncated": False,
        "window": {"from": date_from.isoformat(), "to": date_to.isoformat()},
    }
    if doc_type is not None and not doc_type:
        return {**empty, "shape": "none", "reason": "empty doc_type filter"}
    if activiteit is not None and not activiteit:
        return {**empty, "shape": "none", "reason": "empty activiteit filter"}
    if noise is not None and not noise:
        return {**empty, "shape": "none", "reason": "empty noise filter"}
    if not db.enabled():
        # No Overpass-equivalent exists for this data, so say so instead of
        # answering 0 — an empty list and "we have no source" look identical to a
        # caller and only one of them is true.
        return {**empty, "shape": "unavailable", "reason": "postgres not configured"}

    polygon = await _shape(lng, lat, minutes, profile)
    if polygon is not None:
        shape, geom_args = "isochrone", [json.dumps(polygon)]
        prefix = _HIT_CTE.format(shape_expr=_SHAPE_FROM_GEOJSON)
    else:
        shape = "circle"
        geom_args = [lng, lat, minutes * SPEED_MAP.get(profile, 80)]
        prefix = _HIT_CTE.format(shape_expr=_SHAPE_FROM_RADIUS)

    args = [date_from, date_to, doc_type, activiteit, noise, *geom_args]
    # Interpolated, not parameterised, because an ORDER BY cannot be a bind parameter -- and
    # it is a lookup in a fixed dict, never caller text, so there is still exactly one query
    # text per branch for the planner to cache.
    sql = prefix + _FACETS_AND_ITEMS.format(
        limit=MAX_ITEMS, order=_ORDERS.get(sort, _ORDERS["recent"]))
    async with db.acquire() as conn:
        row = await conn.fetchrow(sql, *args)

    total, items = row["total"], row["items"]
    logger.info(
        "\u2190 bekendmakingen",
        extra={"shape": shape, "total": total, "returned": len(items),
               "by_noise": {b["noise"]: b["count"] for b in row["by_noise"]}},
    )
    return {
        "shape": shape,
        "sort": sort if sort in _ORDERS else "recent",
        "window": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "total": total,
        "months": row["months"],
        "by_type": row["by_type"],
        "by_activiteit": row["by_activiteit"],
        "by_stage": row["by_stage"],
        "by_noise": row["by_noise"],
        "noise_models": row["noise_models"],
        "truncated": total > len(items),
        "items": items,
    }
