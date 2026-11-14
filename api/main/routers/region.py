from fastapi import APIRouter, HTTPException

from ..domains.region import service
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger

router = APIRouter()
logger = get_logger("router.region")


@router.get("/api/main/region")
async def get_region(lng: float, lat: float):
    logger.info("handler called", extra={"lng": lng, "lat": lat})
    try:
        return await service.get_region(lng, lat)
    except (ExternalServiceError, HTTPException):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e
