from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from ..domains.bekendmaking import service
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger

router = APIRouter()
logger = get_logger("router.bekendmakingen")

# The window defaults to a year back from today rather than to the data's own extent:
# a default that silently widens as the table grows would make two callers a month
# apart get different answers from the same URL.
DEFAULT_WINDOW_DAYS = 365

# A 24 km shape over a year matches 54,498 publications (measured), so both axes are
# bounded. 60 minutes driving already reaches well past the Noord-Holland extract the
# self-hosted ORS was built from, so a larger value could not be answered honestly anyway.
MAX_MINUTES = 60
MAX_WINDOW_DAYS = 730


@router.get("/api/main/bekendmakingen")
async def get_bekendmakingen(
    lng: float,
    lat: float,
    minutes: int = Query(10, ge=1, le=MAX_MINUTES),
    profile: str = "walking",
    date_from: date | None = Query(None, description="inclusive; defaults to 365 days ago"),
    date_to: date | None = Query(None, description="inclusive; defaults to today"),
    doc_type: list[str] | None = Query(
        None,
        description="`bekendmaking.doc_type` values; repeat the param. Omitting it means "
        "every type; an explicitly empty list matches nothing.",
    ),
    activiteit: list[str] | None = Query(
        None,
        description="`bekendmaking.activiteit` values, matched as an overlap; repeat the "
        "param. Only omgevingsvergunning rows carry any, so e.g. `bouwen` implies that type.",
    ),
    building_permits: bool = Query(
        False,
        description="Shorthand for doc_type=omgevingsvergunning & activiteit=bouwen. "
        "Ignored if either filter is given explicitly.",
    ),
):
    to_date = date_to or date.today()
    from_date = date_from or (to_date - timedelta(days=DEFAULT_WINDOW_DAYS))
    if from_date > to_date:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    if (to_date - from_date).days > MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=422, detail=f"window must be at most {MAX_WINDOW_DAYS} days"
        )
    if building_permits and doc_type is None and activiteit is None:
        doc_type = service.BUILDING_PERMIT["doc_type"]
        activiteit = service.BUILDING_PERMIT["activiteit"]

    logger.info(
        "handler called",
        extra={
            "lng": lng, "lat": lat, "minutes": minutes, "profile": profile,
            "date_from": from_date.isoformat(), "date_to": to_date.isoformat(),
            "doc_type": doc_type, "activiteit": activiteit,
        },
    )
    try:
        return await service.get_bekendmakingen(
            lng, lat, from_date, to_date, minutes, profile, doc_type, activiteit
        )
    except (ExternalServiceError, HTTPException):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e
