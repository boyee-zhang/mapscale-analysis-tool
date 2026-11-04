"""Free-text semantic search over official publications."""
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from ..domains.search import service
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger

router = APIRouter()
logger = get_logger("router.notice_search")

DEFAULT_WINDOW_DAYS = 365
MAX_MINUTES = 60


@router.get("/api/main/search/notices")
async def search_notices(
    q: str = Query(..., min_length=2, max_length=300,
                   description="Free text, any language the encoder covers. Dutch and "
                               "English both work; the model is multilingual."),
    lng: float | None = Query(None, description="With lat, restricts to the isochrone"),
    lat: float | None = Query(None),
    minutes: int = Query(10, ge=1, le=MAX_MINUTES),
    profile: str = Query("walking"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    doc_type: list[str] | None = Query(None),
    limit: int = Query(20, ge=1, le=service.MAX_LIMIT),
):
    to_date = date_to or date.today()
    from_date = date_from or (to_date - timedelta(days=DEFAULT_WINDOW_DAYS))
    if from_date > to_date:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    if (lng is None) != (lat is None):
        # Half a coordinate is a caller bug, and silently searching the whole country
        # because one parameter was dropped is the worst possible answer to it.
        raise HTTPException(status_code=422, detail="lng and lat must be given together")

    logger.info("handler called", extra={"q": q, "lng": lng, "lat": lat, "minutes": minutes,
                                         "profile": profile, "limit": limit})
    try:
        out = await service.search(q, from_date, to_date, lng, lat, minutes, profile,
                                   doc_type, limit)
    except (ExternalServiceError, HTTPException):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e

    # 503 rather than an empty 200, the same way /metrics does: a missing encoder and a query
    # with no matches are different facts and a caller must be able to tell them apart.
    if out.get("shape") == "unavailable":
        raise HTTPException(status_code=503, detail=out.get("reason", "search unavailable"))
    return out
