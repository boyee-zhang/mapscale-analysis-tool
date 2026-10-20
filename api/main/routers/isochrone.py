from fastapi import APIRouter, HTTPException
from ..domains.geo import service
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger

router = APIRouter()
logger = get_logger("router.isochrone")


@router.get("/api/main/isochrone")
async def get_isochrone(lng: float, lat: float, minutes: int = 10, profile: str = "walking"):
    logger.info("handler called", extra={"lng": lng, "lat": lat, "minutes": minutes, "profile": profile})
    try:
        return await service.get_isochrone(lng, lat, minutes, profile)
    except (ExternalServiceError, HTTPException):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e
