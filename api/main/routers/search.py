from typing import List

from fastapi import APIRouter, HTTPException, Query

from ..domains.address import service
from ..domains.address.service import SearchResult
from ..errors import ExternalServiceError, InternalError
from ..logger import get_logger

logger = get_logger("router.search")
router = APIRouter()


@router.get("/api/main/search", response_model=List[SearchResult])
async def search_address(
    q: str = Query(..., min_length=2, description="Free-text address query"),
    size: int = Query(8, ge=1, le=20),
    w_street:   float = Query(3.0),
    w_city:     float = Query(2.0),
    w_postcode: float = Query(5.0),
    w_label:    float = Query(1.0),
):
    logger.info("search", extra={"q": q, "size": size})
    try:
        return await service.search(q, size, w_street, w_city, w_postcode, w_label)
    except (ExternalServiceError, HTTPException):
        raise
    except Exception as e:
        logger.error("unexpected error", exc_info=True, extra={"error": str(e)})
        raise InternalError(str(e)) from e
