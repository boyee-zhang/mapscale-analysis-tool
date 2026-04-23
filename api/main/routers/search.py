import asyncio
import re
import httpx
from fastapi import APIRouter, Query
from typing import List
from pydantic import BaseModel
from elasticsearch import helpers as es_helpers
from ..clients.es_client import get_es, INDEX
from ..config import ES_URL
from ..logger import get_logger

logger = get_logger("router.search")
router = APIRouter()

PDOK_FREE = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Require ES max_score to exceed this threshold — low scores mean "Diemen" city-only
# partial hits that should fall through to PDOK/Nominatim instead.
ES_MIN_SCORE = 15.0

_WKT = re.compile(r"POINT\(([0-9.]+)\s+([0-9.]+)\)")
_NOMINATIM_HEADERS = {"User-Agent": "mapscale/1.0 (https://github.com/mapscale)"}


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
    """PDOK Locatieserver — Dutch address data (BAG). Has coordinates."""
    params = {"q": q, "fq": "type:adres", "rows": size, "fl": "*"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(PDOK_FREE, params=params)
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
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
    """Nominatim (OSM) — knows company names, POIs, landmarks."""
    params = {
        "q": q,
        "format": "json",
        "limit": size,
        "countrycodes": "nl",
        "addressdetails": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=_NOMINATIM_HEADERS) as client:
            resp = await client.get(NOMINATIM_URL, params=params)
            resp.raise_for_status()
            docs = resp.json()
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
    es = get_es()
    try:
        await es_helpers.async_bulk(es, actions, raise_on_error=False)
        logger.info("results indexed into es", extra={"count": len(actions)})
    except Exception as e:
        logger.warning("failed to index results", extra={"error": str(e)})
    finally:
        await es.close()


@router.get("/api/main/search", response_model=List[SearchResult])
async def search_address(
    q: str = Query(..., min_length=2, description="Free-text address query"),
    size: int = Query(8, ge=1, le=20),
    w_street:   float = Query(3.0),
    w_city:     float = Query(2.0),
    w_postcode: float = Query(5.0),
    w_label:    float = Query(1.0),
):
    logger.info("search", extra={"q": q, "size": size})

    # 1. ES — fast, covers previously searched addresses (skipped if ES_URL not configured)
    hits, max_score = [], 0
    if ES_URL:
        es = get_es()
        try:
            resp = await es.search(index=INDEX, body=_build_es_query(q, size, w_street, w_city, w_postcode, w_label))
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

    # Discard PDOK results if none of the non-trivial query tokens appear in any label.
    # e.g. "OurCampus Diemen" → PDOK returns Diemen addresses but "OurCampus" is absent.
    q_tokens = [t.lower() for t in q.split() if len(t) > 3]
    def _pdok_relevant(results: list[dict]) -> bool:
        if not results or not q_tokens:
            return bool(results)
        labels_text = " ".join(r["label"].lower() for r in results)
        matched = sum(1 for t in q_tokens if t in labels_text)
        return matched == len(q_tokens)

    if pdok_results and _pdok_relevant(pdok_results):
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
