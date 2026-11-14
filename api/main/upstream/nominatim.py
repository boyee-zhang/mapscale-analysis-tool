import httpx

from .. import http_client
from ..errors import ExternalServiceError
from ..logger import get_logger

logger = get_logger("client.nominatim")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy requires an identifying User-Agent. A request without one can be
# refused, so this is not decoration.
_HEADERS = {"User-Agent": "mapscale/1.0 (https://github.com/mapscale)"}


async def search(q: str, size: int) -> list[dict]:
    """Free-text search, as Nominatim returns it. The caller maps the fields.

    Split out of routers/search.py (2026-11-14). Raises rather than swallowing: whether a dead
    tier means "skip it" is the waterfall's decision, and it is made in
    domains/address/service.py.
    """
    params = {
        "q": q,
        "format": "json",
        "limit": size,
        "countrycodes": "nl",
        "addressdetails": 0,
    }
    try:
        resp = await http_client.get().get(NOMINATIM_URL, params=params, timeout=8.0, headers=_HEADERS)
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        logger.warning("search timeout", extra={"service": "nominatim"})
        raise ExternalServiceError("nominatim", 504, "request timed out")
    except httpx.HTTPStatusError as e:
        logger.warning("search http error",
                       extra={"service": "nominatim", "upstream_status": e.response.status_code})
        raise ExternalServiceError("nominatim", e.response.status_code, e.response.text[:200])
    except httpx.RequestError as e:
        logger.warning("search network error", extra={"service": "nominatim", "error": str(e)})
        raise ExternalServiceError("nominatim", 502, f"network error: {e}")
