import asyncio
from fastapi import APIRouter, HTTPException
from ..clients.ors import fetch_directions
from ..errors import ExternalServiceError, InternalError
from ..observability.logger import get_logger

router = APIRouter()
logger = get_logger("router.directions")


@router.get("/api/main/directions")
async def get_directions(
    start_lng: float, start_lat: float, end_lng: float, end_lat: float, mode: str = "walking"
):
    logger.info("handler called", extra={"mode": mode})
    try:
        return await fetch_directions(start_lng, start_lat, end_lng, end_lat, mode)
    except (ExternalServiceError, HTTPException):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e


@router.get("/api/main/bulk_directions")
async def get_bulk_directions(
    start_lng: float, start_lat: float, targets: str, mode: str = "walking"
):
    """targets format: 'lng1,lat1;lng2,lat2;...'"""
    target_list = [t.split(",") for t in targets.split(";")]
    logger.info("bulk handler called", extra={"target_count": len(target_list), "mode": mode})

    async def fetch_one(end_lng, end_lat):
        try:
            return await fetch_directions(start_lng, start_lat, float(end_lng), float(end_lat), mode)
        except Exception:
            return None

    results = await asyncio.gather(*[fetch_one(t[0], t[1]) for t in target_list], return_exceptions=True)
    return {
        f"{t[0]},{t[1]}": r
        for t, r in zip(target_list, results)
        if r and not isinstance(r, Exception)
    }
