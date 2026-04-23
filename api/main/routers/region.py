from fastapi import APIRouter, HTTPException
from ..cache import kv_get, kv_set
from ..clients.pdok import fetch_region
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger

router = APIRouter()
logger = get_logger("router.region")


@router.get("/api/main/region")
async def get_region(lng: float, lat: float):
    logger.info("handler called", extra={"lng": lng, "lat": lat})
    try:
        cache_key = f"region:{lng:.3f}:{lat:.3f}"
        if cached := await kv_get(cache_key):
            logger.info("cache hit", extra={"cache_key": cache_key})
            return cached
        result = await fetch_region(lng, lat)
        await kv_set(cache_key, result, ttl_seconds=604800)
        return result
    except (ExternalServiceError, HTTPException):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e
