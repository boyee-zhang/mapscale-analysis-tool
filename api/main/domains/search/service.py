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

# THE RELEVANCE MARGIN: below this, a result is padding rather than an answer.
#
# A nearest-neighbour query ALWAYS returns `limit` rows. It has no notion of "nothing here
# matches" -- when only three publications in reach are about music festivals, ranks 4 to 30 come
# back anyway, and three answers followed by twenty-seven shop-signage permits reads as a broken
# search even though the ranking was right. This is what separates the two.
#
# IT IS A MARGIN OVER A PER-QUERY BASELINE, NOT AN ABSOLUTE CUTOFF, AND THAT IS THE WHOLE POINT.
# A plain `similarity >= 0.810` was built first and measured, and it is LANGUAGE-BIASED. The same
# question, asked in the three languages a resident might use, inside the same isochrone:
#
#     muziekfestival                            top 0.850    <- Dutch, same language as the corpus
#     music festival                            top 0.822
#     音乐节                                     top 0.782    <- the right event still ranks FIRST
#
# The ranking is correct in all three (the judged set is entirely Chinese and the embedding still
# wins there by +0.194 nDCG). What shifts is the absolute scale: multilingual-e5 places a
# cross-language pair further apart than a same-language pair even when both are about the same
# thing. So a single global number quietly makes the floor strictest for whoever is not writing
# Dutch, and this project's user types Chinese.
#
# `background_similarity()` measures the offset instead of assuming it away: the query's mean
# similarity to a fixed, deterministic sample of the corpus. Measured, that baseline moves
# 0.731-0.780 across the queries above -- a 0.050 spread, the same size as the effect itself, and
# it tracks the language exactly (Dutch 0.753-0.780, English 0.742-0.757, Chinese 0.731-0.767).
# Subtracting it is what makes one constant mean the same thing in every language.
#
# 0.055 IS MEASURED against docs/pg-notes/semsearch-judgeset.md (40 queries, 1,085 graded pairs).
# Three findings, none of them guessable, and one of them a correction:
#
#   * The reflex rules do not work. "Keep everything within X of the top score" and "cut at the
#     first big gap" both leave precision at 0.69 -- exactly where no cut at all leaves it --
#     because they discard relevant and irrelevant rows in the same proportion. The score
#     ordering is noisy, so a GAP is not a relevance boundary.
#   * Centring strictly dominates the absolute floor. At matched precision: the absolute floor
#     reaches P 0.732 while losing 29 relevant rows and emptying 2 queries; the margin reaches
#     P 0.748 losing 17 and emptying 1. Better on all three at once, before counting the language
#     bias the absolute version also carries.
#   * A STRICTER margin is not better. Pushed to 0.060 it reaches P 0.789 but loses 38 of 278
#     relevant rows and empties 4 queries; at 0.065, 103 lost and 9 emptied. 0.050 keeps 98% of
#     relevant rows (5 lost, 1 query emptied) at P 0.740, which is the knee.
#   * NORMALISING BY THE SPREAD MAKES IT WORSE. A z-score, (sim - mu) / sd over the same sample,
#     was the obvious next move once centring turned out not to remove the cross-language gap
#     entirely -- cross-language matching compresses the whole distribution, so correcting the
#     location without the scale should under-correct. Measured, it loses: at matched loss the
#     z-score reaches P 0.703 against the margin's 0.740, and at matched precision it loses 66
#     relevant rows against 38. The background sd moves 0.013-0.030 across queries and that
#     movement is simply not aligned with relevance. The plain margin wins.
#
# So this is deliberately CONSERVATIVE. Its job is to mark the tail as padding, not to make the
# relevance judgement -- which is why nothing is dropped: `items` still carries every row and
# `strong` says how many cleared the margin, so a caller shows the answer and keeps the weak tail
# behind an explicit disclosure instead of deciding for the reader.
#
# CALIBRATED CORPUS-WIDE, CARRIED OVER TO THE SPATIAL MODE. The judged set is corpus-wide, so
# 0.050 is measured against a corpus baseline; within a shape the baseline is the local
# population (see below) and the constant is carried across, sanity-checked on known cases but
# not calibrated for it. The known cost of that: a short cross-language query inside a small
# isochrone can come back with nothing offered even when its top hit is right -- `音乐节` in
# central Amsterdam sits at a margin of 0.038 with a correct Hofjesconcert on top. The weak tail
# is what covers this, which is a large part of why nothing is dropped.
#
# Re-measure with scripts/experiments/judgeset_score.py if the model or the prefixes change.
RELEVANCE_MARGIN = 0.050

# THE BASELINE IS THE POPULATION THAT WAS SEARCHED, and the two modes therefore need two
# baselines. This was got wrong once, in the way this project's notes keep warning about: a
# measured result has a scale of validity, and the corpus-wide baseline was applied to the
# isochrone mode unchanged. The symptom was precise -- inside a 15-minute walk of central
# Amsterdam, `音乐节` returned ZERO answers while its top hit (a Hofjesconcert) was correct.
# Nothing was wrong with the margin; it was being measured against the wrong zero. Corpus-wide,
# the best match for that query scores 0.85, because the best concert in the country is in the
# population. Inside one isochrone the best available document is simply weaker, and subtracting
# a national baseline from a local maximum compares two different things.
#
# So: within a shape the baseline is the mean over the documents inside that shape, and
# corpus-wide it is the mean over a fixed sample of the corpus. Both then answer the same
# question -- "how far above a typical document in the set I actually searched is this one" --
# which is the question the cut needs answered.
#
# IT IS NOT FREE AND IT IS WORTH IT. Measured over 6 queries x 2 cities, the local baseline
# differs from the corpus one by -0.009 to +0.025, and 0.025 is HALF the margin -- a corpus
# baseline near Utrecht makes a query about felling look 0.025 more relevant than it is, which
# lets padding through in exactly the direction this cut exists to stop. The price is a second
# pass over the filtered set: 120-350 ms warm (846 ms on the first, cold -- discard that one, per
# this project's own rule). The way to stop paying it is to compute the mean and the ranking in
# ONE pass -- a `scored` CTE referenced twice, which PostgreSQL materializes -- rather than two
# queries over the same set. Not done here: it rewrites the SQL whose plan shape the
# `hnsw.iterative_scan` guard below depends on, and that guard was bought with a zero-rows bug.
_BACKGROUND_SELECT = ("SELECT avg(1 - (e.embedding <=> $1::vector))\n"
                      "FROM bekendmaking_embedding e\nJOIN bekendmaking b USING (identifier)\n")

# A deterministic spread, not `ORDER BY random()` and not a leading slice of the table. The
# baseline must be the SAME sample on every request or it becomes noise in the one number the cut
# depends on; and it must not be one corner of the corpus, because a baseline measured against
# 3,000 Amsterdam APV templates is a baseline against one municipality's boilerplate. `hashtext`
# spreads it: measured, 3,090 rows across 227 of the 294 municipalities, at 14-20 ms.
BACKGROUND_CORPUS = _BACKGROUND_SELECT + "WHERE mod(abs(hashtext(e.identifier)), 31) = 7"


def _background_spatial(shape_expr: str) -> str:
    """Mean similarity over exactly the rows the ranking was taken from."""
    return (_SPATIAL_CTE.format(shape_expr=shape_expr).rstrip().rstrip(",") + "\n"
            + _BACKGROUND_SELECT + "JOIN hit USING (identifier)\nWHERE " + _WHERE_COMMON)


async def background_similarity(conn, sql: str, args) -> float | None:
    """This query's zero point against the population it searched."""
    try:
        mu = await conn.fetchval(sql, *args)
        return float(mu) if mu is not None else None
    except Exception as e:
        # Not fatal: without a baseline the split is unknown, and saying so is better than
        # falling back to an absolute number whose bias is the reason this function exists.
        logger.warning("background similarity unavailable; results cannot be split into "
                       "answers and padding", extra={"error": str(e)})
        return None

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


# ------------------------------------------------------------------------------------------
# The HNSW index silently returns ZERO rows for a filtered search, and this is the guard.
#
# MEASURED, 10-minute isochrone over central Amsterdam, 3,292 publications inside it:
#
#     LIMIT   default   iterative_scan=strict_order
#         1         0                             1
#         3         0                             3
#         5         5                             5
#        10+   correct                       correct
#
# The plan says why. The planner drives the join from the HNSW index as the OUTER loop —
# walking global nearest neighbours in distance order and testing each against the isochrone —
# so it reports `Rows Removed by Join Filter: 131680` and then runs out. An HNSW scan returns a
# BOUNDED number of candidates (ef_search, 40 by default); when none of them fall inside the
# filter, the query ends with no rows rather than looking further. The result is not slow, it
# is WRONG, and it is wrong only at small LIMIT and only when the planner happens to choose
# that shape — which is the worst way for a bug to behave.
#
# `hnsw.iterative_scan` is pgvector's answer: keep scanning until enough rows survive the
# filter. `strict_order` rather than `relaxed_order` because the endpoint returns a similarity
# per row and sorts by it; relaxed may emit rows slightly out of distance order, which would
# make the numbers disagree with the ordering they are printed in.
#
# Set per query rather than pool-wide in db.py: `hnsw.*` does not exist as a parameter until
# the extension is loaded into the session, so a SET at connection setup fails outright. It is
# applied only to the filtered branch because that is the only one that can be wrong — the
# corpus-wide query has no filter for the index to lose rows against.
_ITERATIVE_SCAN = "SET LOCAL hnsw.iterative_scan = strict_order"


async def _fetch(conn, sql: str, args, spatial: bool):
    if not spatial:
        return await conn.fetch(sql, *args)
    async with conn.transaction():
        try:
            await conn.execute(_ITERATIVE_SCAN)
        except Exception as e:
            # Older pgvector has no iterative scan. Say so once, loudly: the query still runs
            # and may silently under-return, which is exactly what must not pass unnoticed.
            logger.warning("hnsw.iterative_scan unavailable; a filtered search may return "
                           "fewer rows than exist", extra={"error": str(e)})
        return await conn.fetch(sql, *args)


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
        bg_sql, bg_args = BACKGROUND_CORPUS, [qvec]
    else:
        polygon = await _shape(lng, lat, minutes, profile)
        if polygon is not None:
            shape, geom, expr = "isochrone", [json.dumps(polygon)], _SHAPE_FROM_GEOJSON
        else:
            shape = "circle"
            geom = [lng, lat, minutes * SPEED_MAP.get(profile, 80)]
            expr = _SHAPE_FROM_RADIUS
        sql, args = _sql(True, expr, limit), [qvec, date_from, date_to, doc_type, *geom]
        bg_sql, bg_args = _background_spatial(expr), args

    async with db.acquire() as conn:
        rows = await _fetch(conn, sql, args, spatial=shape != "corpus")
        background = await background_similarity(conn, bg_sql, bg_args)

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

    # `items` is ordered by similarity, so the answers are a prefix of it. `strong` is None,
    # not 0, when the baseline could not be measured: "we cannot tell which of these are
    # answers" and "none of these are answers" must not collapse into the same value.
    strong = (None if background is None else
              sum(1 for it in items if it["similarity"] - background >= RELEVANCE_MARGIN))

    logger.info("← search", extra={"shape": shape, "q": q, "returned": len(items),
                                   "strong": strong, "background": background,
                                   "top": items[0]["similarity"] if items else None})
    return {**empty, "shape": shape, "total": len(items), "items": items,
            # The split is served, not applied: dropping the tail here would make "nothing
            # matched well" indistinguishable from "the search broke", and would cost another
            # 200 ms query to get back. `background` is returned so the cut is inspectable --
            # it is the one number that explains why a given row fell on its side of the line.
            "strong": strong, "relevance_margin": RELEVANCE_MARGIN,
            "background": round(background, 4) if background is not None else None}
