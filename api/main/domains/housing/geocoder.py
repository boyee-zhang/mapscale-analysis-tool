"""
Address geocoder backed by PDOK Locatieserver.

Results are persisted in the `geocode_cache` table so they survive a cold start.
When Postgres is not configured this degrades to an in-process dict, which is the
pre-Postgres behaviour.
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

import httpx

from ... import db
from ...logger import get_logger
from .models import HousingListing

logger = get_logger("housing.geocoder")

_PDOK_FREE = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
_WKT = re.compile(r"POINT\(([0-9.]+)\s+([0-9.]+)\)")

# Degraded path only (no DATABASE_URL): normalised key → (lat, lng) or None.
_cache: dict[str, Optional[tuple[float, float]]] = {}

# City-centre fallbacks are re-attempted against PDOK after this long, so a PDOK
# outage cannot pin an approximate coordinate in place forever.
_FALLBACK_TTL = "7 days"

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


# H2S city names that PDOK's Locatieserver does not index, mapped to the official
# Dutch woonplaats name. Querying PDOK for "The Hague" matches Zwolle, so these
# must be translated before lookup and are also what PDOK echoes back.
_PDOK_CITY: dict[str, str] = {
    "The Hague": "'s-Gravenhage",
    "Den Bosch": "'s-Hertogenbosch",
}


def _normalise_address(name: str, city: str) -> str:
    """
    Extract a geocodable address string from an H2S listing name.

    This is the string sent to PDOK, so it stays human-readable.

    Examples
    --------
    "Kastanjelaan 1-108, Eindhoven"  → "Kastanjelaan 1 Eindhoven"
    "Jan van Zutphenstraat 919, Amsterdam" → "Jan van Zutphenstraat 919 Amsterdam"
    "Spui 1, The Hague"              → "Spui 1 's-Gravenhage"
    """
    # Strip city suffix (", City" at the end)
    address = re.sub(rf",?\s*{re.escape(city)}\s*$", "", name, flags=re.IGNORECASE).strip()
    # Remove unit suffix after dash in house number: "1-108" → "1"
    address = re.sub(r"(\d+)-\d+", r"\1", address)
    return f"{address} {_PDOK_CITY.get(city, city)}"


_POSTCODE = re.compile(r"\b(\d{4})\s+([a-z]{2})\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise_key(address: str) -> str:
    """
    Canonical cache key for an address. Pure and deterministic.

    This is the `geocode_cache.address_norm` primary key, so changing the rules
    invalidates the whole table — hence the unit tests in
    tests/unit/test_geocode_key.py.

    Everything is lowercased (including Dutch postcodes, so `1234 AB` becomes
    `1234ab`), punctuation is dropped and runs of whitespace collapse to one space.
    """
    s = address.lower()
    s = _POSTCODE.sub(r"\1\2", s)
    s = _NON_ALNUM.sub(" ", s)
    return s.strip()


async def _pdok_lookup(query: str, expect_city: str) -> Optional[tuple[float, float]]:
    """
    Resolve `query` to (lat, lng) via PDOK, rejecting results in the wrong city.

    PDOK's free search is fuzzy and always returns its best match, so a bad
    address silently resolves to an arbitrary place in the country (a house
    number of 9999 matches postcode 9999XJ in Stitswerd). Since these
    coordinates are now persisted under a stable key, a wrong answer would
    outlive the request — hence the woonplaats check.
    """
    params = {"q": query, "fq": "type:adres", "rows": 1, "fl": "centroide_ll,woonplaatsnaam"}
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

    got_city = docs[0].get("woonplaatsnaam", "")
    if normalise_key(got_city) != normalise_key(expect_city):
        logger.warning("pdok city mismatch, rejecting", extra={
            "query": query, "expected_city": expect_city, "got_city": got_city,
        })
        return None

    m = _WKT.match(docs[0].get("centroide_ll", ""))
    if not m:
        return None
    return float(m.group(2)), float(m.group(1))  # (lat, lng)


_SELECT_CACHED = f"""
SELECT address_norm, ST_Y(geom) AS lat, ST_X(geom) AS lng
FROM geocode_cache
WHERE address_norm = ANY($1::text[])
  AND (source = 'pdok' OR created_at > now() - interval '{_FALLBACK_TTL}')
"""

# A city-centre fallback must never overwrite a precise PDOK coordinate.
_UPSERT_CACHED = """
INSERT INTO geocode_cache (address_norm, geom, source)
VALUES ($1, ST_SetSRID(ST_MakePoint($3, $2), 4326), $4)
ON CONFLICT (address_norm) DO UPDATE
SET geom = EXCLUDED.geom, source = EXCLUDED.source, created_at = now()
WHERE geocode_cache.source <> 'pdok' OR EXCLUDED.source = 'pdok'
"""


async def _db_lookup(keys: list[str]) -> dict[str, tuple[float, float]]:
    """Batch cache read. Returns {} on any DB failure — the caller falls back to PDOK."""
    if not keys:
        return {}
    try:
        async with db.acquire() as conn:
            rows = await conn.fetch(_SELECT_CACHED, keys)
    except Exception as e:
        logger.warning("geocode_cache read failed", extra={"error": str(e)})
        return {}
    return {r["address_norm"]: (r["lat"], r["lng"]) for r in rows}


async def _db_store(key: str, lat: float, lng: float, source: str) -> None:
    try:
        async with db.acquire() as conn:
            await conn.execute(_UPSERT_CACHED, key, lat, lng, source)
    except Exception as e:
        logger.warning("geocode_cache write failed", extra={"error": str(e)})


def _cache_key(name: str, city: str) -> str:
    return normalise_key(_normalise_address(name, city))


async def geocode(name: str, city: str) -> Optional[tuple[float, float]]:
    """Resolve an H2S listing name to (lat, lng)."""
    query = _normalise_address(name, city)
    key = normalise_key(query)

    if db.enabled():
        hit = (await _db_lookup([key])).get(key)
        if hit:
            return hit
    elif key in _cache:
        return _cache[key]

    result = await _pdok_lookup(query, _PDOK_CITY.get(city, city))
    source = "pdok"
    if result is None:
        result = CITY_CENTRES.get(city)
        source = "city_centre"
        if result:
            logger.debug("geocode fallback to city centre", extra={"city": city})

    if not db.enabled():
        _cache[key] = result
    elif result:
        await _db_store(key, result[0], result[1], source)

    return result


async def enrich_batch(listings: list[HousingListing]) -> list[HousingListing]:
    """Geocode all listings in parallel. Modifies lat/lng in-place."""
    # One cache read for the whole batch rather than one per listing.
    prefetched: dict[str, tuple[float, float]] = {}
    if db.enabled() and listings:
        keys = list({_cache_key(lst.name, lst.city) for lst in listings})
        prefetched = await _db_lookup(keys)
        logger.info("geocode prefetch", extra={"keys": len(keys), "hits": len(prefetched)})

    async def _enrich(listing: HousingListing) -> None:
        coords = prefetched.get(_cache_key(listing.name, listing.city))
        if coords is None:
            coords = await geocode(listing.name, listing.city)
        if coords:
            listing.lat, listing.lng = coords

    await asyncio.gather(*[_enrich(lst) for lst in listings], return_exceptions=True)
    return listings
