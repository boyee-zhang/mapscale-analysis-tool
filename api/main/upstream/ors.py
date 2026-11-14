import httpx

from .. import http_client
from ..config import ORS_API_KEY, ORS_BASE_URL
from ..errors import ExternalServiceError
from ..logger import get_logger
from ..metrics import track_upstream

logger = get_logger("client.ors")


async def fetch_isochrone(lng: float, lat: float, range_seconds: int, ors_profile: str) -> dict:
    """POST one isochrone request. The caller has already resolved `ors_profile` and the range.

    Transport only: `minutes`, and which ORS profile a mode maps to, are domain facts and live
    in domains/geo/service.py. This module may not know what a mode is.
    """
    url = f"{ORS_BASE_URL}/isochrones/{ors_profile}"
    body = {"locations": [[lng, lat]], "range": [range_seconds], "range_type": "time"}

    try:
        async with track_upstream("ors"):
            resp = await http_client.get().post(
                url, json=body, headers={"Authorization": ORS_API_KEY}, timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("← isochrone", extra={"features": len(data.get("features", []))})
            return data
    except httpx.TimeoutException:
        logger.warning("isochrone timeout", extra={"service": "ors"})
        raise ExternalServiceError("ors", 504, "request timed out")
    except httpx.HTTPStatusError as e:
        logger.warning("isochrone http error", extra={"service": "ors", "upstream_status": e.response.status_code})
        raise ExternalServiceError("ors", e.response.status_code, e.response.text[:200])
    except httpx.RequestError as e:
        logger.warning("isochrone network error", extra={"service": "ors", "error": str(e)})
        raise ExternalServiceError("ors", 502, f"network error: {e}")


async def fetch_directions(
    start_lng: float, start_lat: float, end_lng: float, end_lat: float, ors_profile: str
) -> dict:
    """One route request. `ors_profile` is already resolved by the caller -- see fetch_isochrone."""
    url = f"{ORS_BASE_URL}/directions/{ors_profile}"
    params = {"api_key": ORS_API_KEY, "start": f"{start_lng},{start_lat}", "end": f"{end_lng},{end_lat}"}

    try:
        async with track_upstream("ors"):
            resp = await http_client.get().get(url, params=params, timeout=15.0)
            resp.raise_for_status()
            logger.info("← directions ok")
            return resp.json()
    except httpx.TimeoutException:
        logger.warning("directions timeout", extra={"service": "ors"})
        raise ExternalServiceError("ors", 504, "directions timed out")
    except httpx.HTTPStatusError as e:
        logger.warning("directions http error", extra={"service": "ors", "upstream_status": e.response.status_code})
        raise ExternalServiceError("ors", e.response.status_code, e.response.text[:200])
    except httpx.RequestError as e:
        logger.warning("directions network error", extra={"service": "ors", "error": str(e)})
        raise ExternalServiceError("ors", 502, f"network error: {e}")
