"""
Dutch municipality (gemeente) boundaries for choropleth rendering.

Served from the `municipality` table, seeded once by scripts/seed_municipalities.py.
When Postgres is not configured this falls back to calling the PDOK CBS WFS on
every request, which is the pre-Postgres behaviour.
"""
from __future__ import annotations

import json

import httpx

from ... import db
from ...logger import get_logger

logger = get_logger("housing.boundaries")

WFS_BASE = "https://service.pdok.nl/cbs/gebiedsindelingen/2024/wfs/v1_0"

# One request returns all ~342 municipalities (~300 KB). PDOK's CQL_FILTER is
# unreliable for this service, so the set is narrowed in Python instead.
WFS_PARAMS = {
    "service":      "WFS",
    "version":      "2.0.0",
    "request":      "GetFeature",
    "typeNames":    "gemeente_gegeneraliseerd",
    "outputFormat": "application/json",
    "count":        "500",
    "srsName":      "EPSG:4326",
}

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


_SELECT_BY_NAME = """
SELECT code, name, ST_AsGeoJSON(geom) AS geojson
FROM municipality
WHERE name = ANY($1::text[])
"""


async def fetch_choropleth_geojson(city_counts: dict[str, int]) -> dict:
    """
    Municipality polygons for the given cities, with listing_count and city
    attached to each feature's properties.

    city_counts: {h2s_city_name: listing_count}
    """
    # H2S names → official CBS names, plus the reverse lookup for these cities only.
    pdok_names = [_H2S_TO_PDOK.get(c, c) for c in city_counts]
    pdok_to_h2s_local = {pdok: h2s for h2s, pdok in zip(city_counts.keys(), pdok_names)}

    if db.enabled():
        features = await _from_db(pdok_names, pdok_to_h2s_local, city_counts)
    else:
        features = await _from_wfs(set(pdok_names), pdok_to_h2s_local, city_counts)

    logger.info("choropleth ready", extra={
        "cities_requested": len(city_counts),
        "features_returned": len(features),
        "source": "postgres" if db.enabled() else "pdok_wfs",
    })
    return {"type": "FeatureCollection", "features": features}


async def _from_db(
    pdok_names: list[str],
    pdok_to_h2s: dict[str, str],
    city_counts: dict[str, int],
) -> list[dict]:
    try:
        async with db.acquire() as conn:
            rows = await conn.fetch(_SELECT_BY_NAME, pdok_names)
    except Exception as e:
        # Not fatal: fall back to the upstream WFS rather than return an empty map.
        logger.warning("municipality read failed, falling back to WFS", extra={"error": str(e)})
        return await _from_wfs(set(pdok_names), pdok_to_h2s, city_counts)

    features = []
    for row in rows:
        h2s_name = pdok_to_h2s.get(row["name"], row["name"])
        features.append({
            "type": "Feature",
            "geometry": json.loads(row["geojson"]),
            "properties": {
                "code": row["code"],
                "statnaam": row["name"],
                "city": h2s_name,
                "listing_count": city_counts.get(h2s_name, 0),
            },
        })
    return features


async def _from_wfs(
    pdok_name_set: set[str],
    pdok_to_h2s: dict[str, str],
    city_counts: dict[str, int],
) -> list[dict]:
    """Degraded path used when Postgres is unavailable."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(WFS_BASE, params=WFS_PARAMS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("PDOK WFS fetch failed", extra={"error": str(e)})
        return []

    matched: list[dict] = []
    for feature in data.get("features", []):
        props = feature.setdefault("properties", {})
        pdok_name = props.get("statnaam") or props.get("gemeentenaam") or ""
        if pdok_name not in pdok_name_set:
            continue
        h2s_name = pdok_to_h2s.get(pdok_name, pdok_name)
        props["city"] = h2s_name
        props["listing_count"] = city_counts.get(h2s_name, 0)
        matched.append(feature)
    return matched
