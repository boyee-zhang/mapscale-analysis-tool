"""
Housing data router.

Completely provider-agnostic: all provider details live in the housing package.
The router only knows about models, registry, and geocoder.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from ..config import ETL_CRON_TOKEN, UPSTASH_TOKEN, UPSTASH_URL
from ..errors import ExternalServiceError, InternalError
from ..housing import providers as _  # noqa: F401 — triggers provider self-registration
from ..housing import registry
from ..housing.boundaries import fetch_choropleth_geojson
from ..housing.etl import run as run_etl
from ..housing.geocoder import enrich_batch
from ..housing.upstash import UpstashClient
from ..logger import get_logger

router = APIRouter(prefix="/api/main/housing")
logger = get_logger("router.housing")


def _verify_cron_token(authorization: str = Header(...)):
    expected = f"Bearer {ETL_CRON_TOKEN}"
    if not ETL_CRON_TOKEN or authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid cron token")


@router.get("/providers")
async def list_providers():
    """List all registered housing data providers and their metadata."""
    return [
        {
            "id": p.provider_id,
            "name": p.display_name,
            "listing_type": p.listing_type,
            "cities": p.supported_cities,
        }
        for p in registry.all_providers()
    ]


def _apply_time_filter(listings, months: int, direction: str):
    today = date.today().isoformat()
    if direction == "past":
        start = (date.today() - timedelta(days=30 * months)).isoformat()
        return [l for l in listings if l.available_from is not None and start <= l.available_from <= today]
    else:
        cutoff = (date.today() + timedelta(days=30 * months)).isoformat()
        return [l for l in listings if l.available_from is None or today <= l.available_from <= cutoff]


@router.get("/listings")
async def get_listings(
    provider: str = Query("h2s", description="Provider ID"),
    cities: Optional[str] = Query(None, description="Comma-separated city names; omit for all"),
    months: int = Query(3, ge=1, le=12),
    direction: Literal["past", "future"] = Query("future", description="'future': available in next N months; 'past': released in last N months"),
):
    """
    GeoJSON FeatureCollection of individual listings with coordinates.

    Geocoding is cached in-memory; first call per process may be slower.
    """
    p = registry.get(provider)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not registered.")

    city_list = [c.strip() for c in cities.split(",")] if cities else None
    logger.info("housing listings", extra={"provider": provider, "cities": city_list, "months": months, "direction": direction})

    try:
        listings = await p.fetch_listings(city_list)
    except ExternalServiceError:
        raise
    except Exception as e:
        logger.error("fetch_listings failed", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e

    listings = _apply_time_filter(listings, months, direction)
    await enrich_batch(listings)

    features = [l.to_geojson_feature() for l in listings if l.has_coords]
    logger.info("housing listings ready", extra={"total": len(listings), "geocoded": len(features)})

    return {"type": "FeatureCollection", "features": features}


@router.get("/choropleth")
async def get_choropleth(
    provider: str = Query("h2s"),
    months: int = Query(3, ge=1, le=12),
    direction: Literal["past", "future"] = Query("future"),
):
    """
    GeoJSON FeatureCollection of Dutch municipality polygons for choropleth rendering.

    Each feature contains:
      - geometry: municipality polygon (simplified, from PDOK CBS WFS)
      - properties.city: H2S city name
      - properties.listing_count: number of listings within the time window
    """
    p = registry.get(provider)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not registered.")

    logger.info("housing choropleth", extra={"provider": provider, "months": months, "direction": direction})

    counts: dict[str, int] = {city: 0 for city in p.supported_cities}

    if direction == "past":
        if not UPSTASH_URL or not UPSTASH_TOKEN:
            raise HTTPException(status_code=503, detail="Upstash not configured — run ETL setup first")
        redis = UpstashClient(UPSTASH_URL, UPSTASH_TOKEN)
        past_counts = await redis.get_past_city_counts(months)
        logger.info("choropleth past from upstash", extra={"cities_with_data": len(past_counts)})
        counts.update({k: v for k, v in past_counts.items() if k in counts})
    else:
        try:
            listings = await p.fetch_listings()
        except ExternalServiceError:
            raise
        except Exception as e:
            logger.error("choropleth fetch failed", exc_info=True, extra={"error": str(e)})
            raise InternalError(str(e)) from e

        listings = _apply_time_filter(listings, months, direction)
        for l in listings:
            if l.city in counts:
                counts[l.city] += 1

    return await fetch_choropleth_geojson(counts)


@router.get("/heatmap")
async def get_heatmap(
    provider: str = Query("h2s"),
    months: int = Query(3, ge=1, le=12),
    direction: Literal["past", "future"] = Query("future"),
):
    """
    GeoJSON FeatureCollection for heatmap rendering.

    Each feature is a listing point; the frontend uses listing density
    to drive heatmap intensity. City-centre fallback is used when
    PDOK geocoding fails for an address.
    """
    p = registry.get(provider)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not registered.")

    logger.info("housing heatmap", extra={"provider": provider, "months": months, "direction": direction})

    try:
        listings = await p.fetch_listings()
    except ExternalServiceError:
        raise
    except Exception as e:
        logger.error("heatmap fetch failed", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e

    listings = _apply_time_filter(listings, months, direction)
    await enrich_batch(listings)

    features = [l.to_geojson_feature() for l in listings if l.has_coords]
    return {"type": "FeatureCollection", "features": features}


@router.post("/etl/run", dependencies=[Depends(_verify_cron_token)])
async def trigger_etl(provider: str = Query("h2s")):
    """Trigger a single ETL snapshot. Called by QStash on schedule."""
    try:
        result = await run_etl(provider)
        return result
    except Exception as e:
        logger.error("etl run failed", exc_info=True, extra={"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))
