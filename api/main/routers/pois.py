from fastapi import APIRouter, HTTPException
from ..cache import kv_get, kv_set
from ..clients.overpass import fetch_pois
from ..errors import ExternalServiceError, InternalError
from ..observability.logger import get_logger

router = APIRouter()
logger = get_logger("router.pois")


@router.get("/api/main/pois")
async def get_pois(lng: float, lat: float, minutes: int = 10, profile: str = "walking"):
    logger.info("handler called", extra={"lng": lng, "lat": lat, "minutes": minutes, "profile": profile})
    try:
        cache_key = f"pois:{lng:.3f}:{lat:.3f}:{minutes}:{profile}"
        if cached := await kv_get(cache_key):
            logger.info("cache hit", extra={"cache_key": cache_key})
            return cached
        data = await fetch_pois(lng, lat, minutes, profile)
        await kv_set(cache_key, data, ttl_seconds=21600)
        return data
    except (ExternalServiceError, HTTPException):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e
