from fastapi import APIRouter, HTTPException
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger
from ..upstream.ors import fetch_directions

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

