"""Address lookup: Elasticsearch, then PDOK, then Nominatim.

Moved out of routers/search.py (2026-11-14), which was 231 lines of which ~190 were this.
Nothing here is new: the ES query, the 15.0 score threshold, both result mappings, the
relevance rule, the fire-and-forget write-back and the order of the three tiers are the code
that was in the router, with the two raw `http_client.get()` calls moved to `upstream/pdok.py`
and the new `upstream/nominatim.py`.

WHY THREE TIERS, since the order is the whole design:

  1. **Elasticsearch** answers from addresses someone has already searched for. Accepted only
     above `ES_MIN_SCORE`, because a low max_score means a city-name-only partial hit
     ("Diemen" for "Diemenstraat 12") that should fall through rather than win.
  2. **PDOK Locatieserver** is the Dutch address registry (BAG). Authoritative for addresses,
     and it does not know company names. Gated by `_pdok_relevant`, which is what stops PDOK
     from answering "Albert Heijn Damrak" with a street called Damrak.
  3. **Nominatim** knows company names, POIs and landmarks, and is last because it is a free
     public service on a courtesy basis.

Tiers 2 and 3 write their results back into ES, so the index enriches itself over time and a
repeated query gets tier 1. The write-back is deliberately not awaited: it must not add
latency to the answer, and `raise_on_error=False` means a partial failure degrades the index
rather than the response.

`domains/search/` is a different thing entirely -- semantic search over publications, served
by /search/notices. This domain is the address bar.
"""
from __future__ import annotations

import asyncio
import re

from pydantic import BaseModel

from ...config import ES_URL
from ...logger import get_logger
from ...upstream import nominatim
from ...upstream.es_client import get_es
from ...upstream.pdok import search_addresses
from .index import INDEX

logger = get_logger("domain.address")

# Require ES max_score to exceed this threshold — low scores mean "Diemen" city-only
# partial hits that should fall through to PDOK/Nominatim instead.
ES_MIN_SCORE = 15.0

# PDOK returns the coordinate as WKT in `centroide_ll`, not as a pair of numbers.
_WKT = re.compile(r"POINT\(([0-9.]+)\s+([0-9.]+)\)")


class SearchResult(BaseModel):
    label: str
    lng: float
    lat: float


def _build_es_query(q: str, size: int, w_street: float, w_city: float,
                    w_postcode: float, w_label: float) -> dict:
    return {
        "query": {
            "bool": {
                "should": [
                    {
                        "multi_match": {
                            "query": q,
                            "fields": [
                                f"street^{w_street}",
                                f"city^{w_city}",
                                f"label^{w_label}",
                            ],
                            "fuzziness": "AUTO",
                            "type": "best_fields",
                            # require most query tokens to match — prevents single-word city hits
                            "minimum_should_match": "75%",
                        }
                    },
                    {
                        "term": {
                            "postcode": {
                                "value": q.upper().replace(" ", ""),
                                "boost": w_postcode,
                            }
                        }
                    },
                    {
                        "match_phrase_prefix": {
                            "label": {"query": q, "boost": 2.0}
                        }
                    },
                ]
            }
        },
        "size": size,
    }


async def _pdok_search(q: str, size: int) -> list[dict]:
    """PDOK Locatieserver — Dutch address data (BAG). Has coordinates.

    Any failure returns [] so the waterfall carries on to Nominatim. That broad `except` is
    the policy, not laziness: a tier that cannot answer is a tier that should be skipped.
    """
    try:
        docs = await search_addresses(q, size)
    except Exception as e:
        logger.warning("pdok search failed", extra={"error": str(e)})
        return []

    results = []
    for doc in docs:
        m = _WKT.match(doc.get("centroide_ll", ""))
        if not m:
            continue
        lng, lat = float(m.group(1)), float(m.group(2))
        results.append({
            "id": doc.get("id"),
            "label": doc.get("weergavenaam", ""),
            "street": doc.get("straatnaam", ""),
            "housenumber": doc.get("huis_nlt", ""),
            "postcode": doc.get("postcode", ""),
            "city": doc.get("woonplaatsnaam", ""),
            "municipality": doc.get("gemeentenaam", ""),
            "lng": lng,
            "lat": lat,
        })
    return results


async def _nominatim_search(q: str, size: int) -> list[dict]:
    """Nominatim (OSM) — knows company names, POIs, landmarks. Same skip-on-failure policy."""
    try:
        docs = await nominatim.search(q, size)
    except Exception as e:
        logger.warning("nominatim search failed", extra={"error": str(e)})
        return []

    return [
        {
            "id": f"osm-{doc['osm_type']}-{doc['osm_id']}",
            "label": doc.get("display_name", ""),
            "street": "", "housenumber": "", "postcode": "",
            "city": "", "municipality": "",
            "lng": float(doc["lon"]),
            "lat": float(doc["lat"]),
        }
        for doc in docs
        if doc.get("lat") and doc.get("lon")
    ]


def _pdok_relevant(q: str, results: list[dict]) -> bool:
    """Return True if all long query tokens appear somewhere in the PDOK result labels."""
    q_tokens = [t.lower() for t in q.split() if len(t) > 3]
    if not results or not q_tokens:
        return bool(results)
    labels_text = " ".join(r["label"].lower() for r in results)
    matched = sum(1 for t in q_tokens if t in labels_text)
    return matched == len(q_tokens)


async def _index_results(results: list[dict]) -> None:
    """Store results back into ES for future cache hits. No-op if ES is not configured."""
    if not ES_URL:
        return
    actions = [
        {
            "_index": INDEX,
            "_id": r["id"],
            "_source": {
                "street":       r["street"],
                "housenumber":  r["housenumber"],
                "postcode":     r["postcode"],
                "city":         r["city"],
                "municipality": r["municipality"],
                "label":        r["label"],
                "location":     {"lat": r["lat"], "lon": r["lng"]},
                "source_id":    r["id"],
            },
        }
        for r in results if r.get("id")
    ]
    if not actions:
        return
    from elasticsearch import helpers as es_helpers
    es = get_es()
    try:
        await es_helpers.async_bulk(es, actions, raise_on_error=False)
        logger.info("results indexed into es", extra={"count": len(actions)})
    except Exception as e:
        logger.warning("failed to index results", extra={"error": str(e)})
    finally:
        await es.close()


async def search(q: str, size: int, w_street: float, w_city: float,
                 w_postcode: float, w_label: float) -> list[SearchResult]:
    # 1. ES — fast, covers previously searched addresses (skipped if ES_URL not configured)
    hits, max_score = [], 0
    if ES_URL:
        es = get_es()
        try:
            resp = await es.search(index=INDEX,
                                   body=_build_es_query(q, size, w_street, w_city, w_postcode, w_label))
            hits = resp["hits"]["hits"]
            max_score = resp["hits"].get("max_score") or 0
        except Exception as e:
            logger.error("es search error", extra={"error": str(e)})
        finally:
            await es.close()

    if hits and max_score >= ES_MIN_SCORE:
        logger.info("es hit", extra={"q": q, "count": len(hits), "max_score": max_score})
        return [
            SearchResult(
                label=h["_source"]["label"],
                lng=h["_source"]["location"]["lon"],
                lat=h["_source"]["location"]["lat"],
            )
            for h in hits
        ]

    # 2. PDOK — Dutch address registry, no company names
    logger.info("es miss, trying pdok", extra={"q": q, "max_score": max_score})
    pdok_results = await _pdok_search(q, size)

    if pdok_results and _pdok_relevant(q, pdok_results):
        asyncio.ensure_future(_index_results(pdok_results))
        logger.info("pdok hit", extra={"q": q, "count": len(pdok_results)})
        return [SearchResult(label=r["label"], lng=r["lng"], lat=r["lat"]) for r in pdok_results]

    logger.info("pdok irrelevant, trying nominatim", extra={"q": q})

    # 3. Nominatim (OSM) — company names, landmarks, POIs
    logger.info("pdok miss, trying nominatim", extra={"q": q})
    osm_results = await _nominatim_search(q, size)

    if osm_results:
        asyncio.ensure_future(_index_results(osm_results))
        logger.info("nominatim hit", extra={"q": q, "count": len(osm_results)})

    return [SearchResult(label=r["label"], lng=r["lng"], lat=r["lat"]) for r in osm_results]
