from fastapi import APIRouter
from ..cache import kv_get, kv_set
from ..clients.tomtom import fetch_flow, fetch_incidents
from ..config import TOMTOM_API_KEY
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger

router = APIRouter()
logger = get_logger("router.traffic")


@router.get("/api/main/traffic/tile-url")
async def get_tile_url():
    """Returns TomTom traffic flow tile URL template for MapLibre raster source."""
    base = "https://api.tomtom.com/traffic/map/4/tile/flow/relative0/{z}/{x}/{y}.png"
    return {"flowTileUrl": f"{base}?key={TOMTOM_API_KEY}"}


@router.get("/api/main/traffic/flow")
async def get_flow(lng: float, lat: float):
    """
    Current congestion at a point. Returns currentSpeed, freeFlowSpeed, and a
    congestionFactor (0.0 = free flow, 1.0 = standstill).
    TTL: 5 minutes — traffic changes fast.
    """
    logger.info("handler called", extra={"lng": lng, "lat": lat})
    try:
        cache_key = f"flow:{lng:.3f}:{lat:.3f}"
        if cached := await kv_get(cache_key):
            logger.info("cache hit", extra={"cache_key": cache_key})
            return cached

        flow = await fetch_flow(lat, lng)

        current = flow.get("currentSpeed", 0)
        free = flow.get("freeFlowSpeed", 1)
        congestion_factor = round(1 - (current / free), 2) if free else 0.0

        result = {
            "currentSpeed": current,
            "freeFlowSpeed": free,
            "congestionFactor": congestion_factor,   # 0.0 = clear, 1.0 = standstill
            "confidence": flow.get("confidence"),
        }
        await kv_set(cache_key, result, ttl_seconds=300)  # 5 min TTL
        return result

    except (ExternalServiceError,):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e


@router.get("/api/main/traffic/incidents")
async def get_incidents(min_lng: float, min_lat: float, max_lng: float, max_lat: float):
    """
    Traffic incidents within a bounding box.
    Normalized to GeoJSON FeatureCollection so the frontend can add it directly as a map source.
    TTL: 5 minutes.
    """
    logger.info("handler called", extra={
        "bbox": f"{min_lng},{min_lat},{max_lng},{max_lat}"
    })
    try:
        cache_key = f"incidents:{min_lng:.3f}:{min_lat:.3f}:{max_lng:.3f}:{max_lat:.3f}"
        if cached := await kv_get(cache_key):
            logger.info("cache hit", extra={"cache_key": cache_key})
            return cached

        incidents = await fetch_incidents(min_lng, min_lat, max_lng, max_lat)

        # Normalize to GeoJSON LineString features (TomTom returns coordinates without "type")
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

        await kv_set(cache_key, geojson, ttl_seconds=300)
        return geojson

    except (ExternalServiceError,):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e
