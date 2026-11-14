from fastapi import APIRouter

from ..domains.traffic import service
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger

router = APIRouter()
logger = get_logger("router.traffic")


@router.get("/api/main/traffic/tile-url")
async def get_tile_url():
    """TomTom traffic flow tile URL template for a MapLibre raster source."""
    return service.tile_url()


@router.get("/api/main/traffic/flow")
async def get_flow(lng: float, lat: float):
    """Current congestion at a point: currentSpeed, freeFlowSpeed, congestionFactor (0..1)."""
    logger.info("handler called", extra={"lng": lng, "lat": lat})
    try:
        return await service.get_flow(lng, lat)
    except (ExternalServiceError,):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e


@router.get("/api/main/traffic/incidents")
async def get_incidents(min_lng: float, min_lat: float, max_lng: float, max_lat: float):
    """Traffic incidents in a bounding box, as a GeoJSON FeatureCollection."""
    logger.info("handler called", extra={
        "bbox": f"{min_lng},{min_lat},{max_lng},{max_lat}"
    })
    try:
        return await service.get_incidents(min_lng, min_lat, max_lng, max_lat)
    except (ExternalServiceError,):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e
