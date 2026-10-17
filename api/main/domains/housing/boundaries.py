"""
Dutch municipality (gemeente) boundary fetcher via PDOK CBS WFS.

Returns simplified polygon GeoJSON for a given set of cities,
enriched with listing counts for choropleth rendering.
"""
from __future__ import annotations

import httpx

from ..logger import get_logger

logger = get_logger("housing.boundaries")

_WFS_BASE = "https://service.pdok.nl/cbs/gebiedsindelingen/2024/wfs/v1_0"

# H2S city name → official CBS/PDOK municipality name (only where they differ)
_H2S_TO_PDOK: dict[str, str] = {
    "The Hague":  "'s-Gravenhage",    # CBS statnaam differs from common "Den Haag"
    "Den Bosch":  "'s-Hertogenbosch",
    "Maarssen":   "Stichtse Vecht",   # merged into Stichtse Vecht in 2011
    "Rijswijk":   "Rijswijk (ZH.)",   # CBS disambiguates from Rijswijk NB
    "Sittard":    "Sittard-Geleen",   # merged with Geleen in 2001
    "Velp":       "Rheden",            # Velp is a village in Rheden municipality
}

# Reverse lookup: PDOK name → H2S name
_PDOK_TO_H2S: dict[str, str] = {v: k for k, v in _H2S_TO_PDOK.items()}


async def fetch_choropleth_geojson(city_counts: dict[str, int]) -> dict:
    """
    Fetch municipality polygons from PDOK for the given cities,
    then attach listing_count and city to each feature's properties.

    city_counts: {h2s_city_name: listing_count}
    """
    # Map H2S names → PDOK names for the WFS CQL filter
    pdok_names = [_H2S_TO_PDOK.get(c, c) for c in city_counts]

    # Build lookup: pdok_name → h2s_name (for the cities we care about)
    pdok_to_h2s_local = {pdok: h2s for h2s, pdok in zip(city_counts.keys(), pdok_names)}

    # Fetch all Dutch municipalities in one request (≈342 features, ~300 KB).
    # PDOK WFS CQL_FILTER is unreliable for this service; filter in Python instead.
    params = {
        "service":      "WFS",
        "version":      "2.0.0",
        "request":      "GetFeature",
        "typeNames":    "gemeente_gegeneraliseerd",
        "outputFormat": "application/json",
        "count":        "500",
        "srsName":      "EPSG:4326",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(_WFS_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("PDOK WFS fetch failed", extra={"error": str(e)})
        return {"type": "FeatureCollection", "features": []}

    # Filter to only the cities we care about, then attach listing counts
    pdok_name_set = set(pdok_names)
    matched: list[dict] = []
    for feature in data.get("features", []):
        props = feature.setdefault("properties", {})
        pdok_name = props.get("statnaam") or props.get("gemeentenaam") or ""
        if pdok_name not in pdok_name_set:
            continue
        h2s_name = pdok_to_h2s_local.get(pdok_name, pdok_name)
        props["city"] = h2s_name
        props["listing_count"] = city_counts.get(h2s_name, 0)
        matched.append(feature)

    logger.info("choropleth ready", extra={
        "cities_requested": len(city_counts),
        "features_returned": len(matched),
    })
    return {"type": "FeatureCollection", "features": matched}
