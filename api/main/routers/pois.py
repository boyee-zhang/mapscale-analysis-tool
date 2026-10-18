from fastapi import APIRouter, HTTPException, Query
from ..domains.geo import service
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger

router = APIRouter()
logger = get_logger("router.pois")


@router.get("/api/main/pois")
async def get_pois(
    lng: float,
    lat: float,
    minutes: int = 10,
    profile: str = "walking",
    categories: list[str] | None = Query(
        None,
        description="poi_category keys; repeat the param. Defaults to the legacy "
        "supermarket/convenience/gym set. Ignored when Postgres is unconfigured.",
    ),
):
    logger.info(
        "handler called",
        extra={"lng": lng, "lat": lat, "minutes": minutes, "profile": profile, "categories": categories},
    )
    try:
        return await service.get_pois(lng, lat, minutes, profile, categories)
    except (ExternalServiceError, HTTPException):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e
