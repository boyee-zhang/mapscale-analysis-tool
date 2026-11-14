"""Live congestion and incidents, as the map wants them.

Moved out of routers/traffic.py unchanged (2026-11-14). The router held three domain rules:
the congestion formula, the GeoJSON normalisation TomTom's incident payload needs, and the
tile URL that carries the API key. None of them is HTTP adaptation.

Both cache keys and both TTLs are byte-for-byte what the router used. 5 minutes is not a
round number picked for neatness -- it is the only TTL in this project that is short because
the underlying truth moves that fast.
"""
from __future__ import annotations

from ...cache import kv_get, kv_set
from ...config import TOMTOM_API_KEY
from ...logger import get_logger
from ...upstream.tomtom import fetch_flow, fetch_incidents

logger = get_logger("domain.traffic")

TTL_SECONDS = 300

# MapLibre takes a raster source as a URL template, so the key has to be in the URL. It is
# the browser that fetches these tiles; this is a public-by-design key, not a leak.
_TILE_BASE = "https://api.tomtom.com/traffic/map/4/tile/flow/relative0/{z}/{x}/{y}.png"


def tile_url() -> dict:
    """The TomTom flow tile template for a MapLibre raster source."""
    return {"flowTileUrl": f"{_TILE_BASE}?key={TOMTOM_API_KEY}"}


async def get_flow(lng: float, lat: float) -> dict:
    """Congestion at a point: current and free-flow speed, plus a 0..1 congestion factor."""
    cache_key = f"flow:{lng:.3f}:{lat:.3f}"
    if cached := await kv_get(cache_key):
        logger.info("cache hit", extra={"cache_key": cache_key})
        return cached

    flow = await fetch_flow(lat, lng)

    current = flow.get("currentSpeed", 0)
    free = flow.get("freeFlowSpeed", 1)
    # `if free else 0.0` guards a division, and the 0.0 is the honest answer: with no
    # free-flow reference there is nothing to be congested relative to.
    congestion_factor = round(1 - (current / free), 2) if free else 0.0

    result = {
        "currentSpeed": current,
        "freeFlowSpeed": free,
        "congestionFactor": congestion_factor,   # 0.0 = clear, 1.0 = standstill
        "confidence": flow.get("confidence"),
    }
    await kv_set(cache_key, result, ttl_seconds=TTL_SECONDS)
    return result


async def get_incidents(min_lng: float, min_lat: float, max_lng: float, max_lat: float) -> dict:
    """Incidents in a bbox as a GeoJSON FeatureCollection the frontend can add as a source."""
    cache_key = f"incidents:{min_lng:.3f}:{min_lat:.3f}:{max_lng:.3f}:{max_lat:.3f}"
    if cached := await kv_get(cache_key):
        logger.info("cache hit", extra={"cache_key": cache_key})
        return cached

    incidents = await fetch_incidents(min_lng, min_lat, max_lng, max_lat)

    # TomTom returns coordinates with no "type", so these are not GeoJSON features yet.
    # Incidents without coordinates are dropped rather than emitted as a null geometry:
    # MapLibre treats a malformed feature as a source-level error and draws nothing at all.
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": inc["geometry"]["coordinates"],
                },
                "properties": inc.get("properties", {}),
            }
            for inc in incidents
            if inc.get("geometry", {}).get("coordinates")
        ],
    }

    await kv_set(cache_key, geojson, ttl_seconds=TTL_SECONDS)
    return geojson
