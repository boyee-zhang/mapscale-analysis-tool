from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from ..domains.bekendmaking import service
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger

router = APIRouter()
logger = get_logger("router.bekendmakingen")

# The window and shape limits are the domain's; this router only enforces them, via Query()
# and the two 422s below. What each number means and why is in the service.
DEFAULT_WINDOW_DAYS = service.DEFAULT_WINDOW_DAYS
MAX_MINUTES = service.MAX_MINUTES
MAX_WINDOW_DAYS = service.MAX_WINDOW_DAYS


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
    noise: list[str] | None = Query(
        None,
        description="Filter by the noise classifier: `noisy`, `quiet` or `unscored`; repeat "
        "the param. Omitting it means every bucket. `unscored` is its own bucket, not part "
        "of `quiet` — it means no score exists yet, e.g. a publication imported after the "
        "last run of etl/derive/noise_score.py. The response's `noise_models` carries "
        "the threshold and its measured precision/recall, so a caller can weight the verdict.",
    ),
    sort: str = Query(
        "recent",
        pattern="^(recent|noise)$",
        description="`recent` (default) or `noise`. This matters because `items` is capped at "
        "500: ordering by date fills the cap with the newest publications, so asking for the "
        "loudest requires `sort=noise`. `total` and the facets are unaffected either way.",
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
    doc_type, activiteit = service.resolve_filters(building_permits, doc_type, activiteit)

    logger.info(
        "handler called",
        extra={
            "lng": lng, "lat": lat, "minutes": minutes, "profile": profile,
            "date_from": from_date.isoformat(), "date_to": to_date.isoformat(),
            "doc_type": doc_type, "activiteit": activiteit, "noise": noise,
            "sort": sort,
        },
    )
    try:
        return await service.get_bekendmakingen(
            lng, lat, from_date, to_date, minutes, profile, doc_type, activiteit, noise, sort
        )
    except (ExternalServiceError, HTTPException):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e
