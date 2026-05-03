"""
Address geocoder backed by PDOK Locatieserver.

Results are cached in-memory for the process lifetime — H2S manages a fixed set
of buildings, so the same addresses recur across requests.
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

import httpx

from ..logger import get_logger
from .models import HousingListing

logger = get_logger("housing.geocoder")

_PDOK_FREE = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
_WKT = re.compile(r"POINT\(([0-9.]+)\s+([0-9.]+)\)")

# In-memory cache: normalised address string → (lat, lng) or None if not found.
_cache: dict[str, Optional[tuple[float, float]]] = {}

# Known Dutch city centre coordinates — used as geocoding fallback and for
# providers that only know the city name (no street address).
CITY_CENTRES: dict[str, tuple[float, float]] = {
    "Amersfoort":             (52.1561, 5.3878),
    "Amsterdam":              (52.3676, 4.9041),
    "Arnhem":                 (51.9851, 5.8987),
    "Capelle aan den IJssel": (51.9271, 4.5699),
    "Delft":                  (52.0116, 4.3571),
    "Den Bosch":              (51.6978, 5.3037),
    "Diemen":                 (52.3338, 4.9607),
    "Dordrecht":              (51.8133, 4.6901),
    "Eindhoven":              (51.4416, 5.4697),
    "Groningen":              (53.2194, 6.5665),
    "Haarlem":                (52.3874, 4.6462),
    "Helmond":                (51.4817, 5.6614),
    "Leiden":                 (52.1601, 4.4970),
    "Maarssen":               (52.1355, 5.0442),
    "Maastricht":             (50.8514, 5.6910),
    "Nieuwegein":             (52.0349, 5.0862),
    "Nijmegen":               (51.8426, 5.8546),
    "Rijswijk":               (52.0411, 4.3220),
    "Rotterdam":              (51.9225, 4.4792),
    "Sittard":                (51.0007, 5.8697),
    "The Hague":              (52.0705, 4.3007),
    "Tilburg":                (51.5555, 5.0913),
    "Utrecht":                (52.0907, 5.1214),
    "Velp":                   (51.9960, 5.9989),
    "Zeist":                  (52.0871, 5.2300),
    "Zoetermeer":             (52.0574, 4.4938),
}


def _normalise_address(name: str, city: str) -> str:
    """
    Extract a geocodable address string from an H2S listing name.

    Examples
    --------
    "Kastanjelaan 1-108, Eindhoven"  → "Kastanjelaan 1 Eindhoven"
    "Jan van Zutphenstraat 919, Amsterdam" → "Jan van Zutphenstraat 919 Amsterdam"
    """
    # Strip city suffix (", City" at the end)
    address = re.sub(rf",?\s*{re.escape(city)}\s*$", "", name, flags=re.IGNORECASE).strip()
    # Remove unit suffix after dash in house number: "1-108" → "1"
    address = re.sub(r"(\d+)-\d+", r"\1", address)
    return f"{address} {city}"


async def _pdok_lookup(query: str) -> Optional[tuple[float, float]]:
    params = {"q": query, "fq": "type:adres", "rows": 1, "fl": "centroide_ll"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_PDOK_FREE, params=params)
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
    except Exception as e:
        logger.warning("pdok lookup failed", extra={"query": query, "error": str(e)})
        return None

    if not docs:
        return None
    m = _WKT.match(docs[0].get("centroide_ll", ""))
    if not m:
        return None
    return float(m.group(2)), float(m.group(1))  # (lat, lng)


async def geocode(name: str, city: str) -> Optional[tuple[float, float]]:
    """Resolve an H2S listing name to (lat, lng), with in-memory cache."""
    key = _normalise_address(name, city)
    if key in _cache:
        return _cache[key]

    result = await _pdok_lookup(key)
    if result is None:
        result = CITY_CENTRES.get(city)
        if result:
            logger.debug("geocode fallback to city centre", extra={"city": city})

    _cache[key] = result
    return result


async def enrich_batch(listings: list[HousingListing]) -> list[HousingListing]:
    """Geocode all listings in parallel. Modifies lat/lng in-place."""
    async def _enrich(listing: HousingListing) -> None:
        coords = await geocode(listing.name, listing.city)
        if coords:
            listing.lat, listing.lng = coords

    await asyncio.gather(*[_enrich(l) for l in listings], return_exceptions=True)
    return listings
