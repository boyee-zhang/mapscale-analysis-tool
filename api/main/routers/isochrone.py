from fastapi import APIRouter, HTTPException
from ..cache import kv_get, kv_set
from ..clients.ors import fetch_isochrone
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger

router = APIRouter()
logger = get_logger("router.isochrone")


@router.get("/api/main/isochrone")
async def get_isochrone(lng: float, lat: float, minutes: int = 10, profile: str = "walking"):
    logger.info("handler called", extra={"lng": lng, "lat": lat, "minutes": minutes, "profile": profile})
    try:
        cache_key = f"iso:{lng:.5f}:{lat:.5f}:{minutes}:{profile}"
        if cached := await kv_get(cache_key):
            logger.info("cache hit", extra={"cache_key": cache_key})
            return cached
        data = await fetch_isochrone(lng, lat, minutes, profile)
        await kv_set(cache_key, data, ttl_seconds=86400)
        return data
    except (ExternalServiceError, HTTPException):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e
