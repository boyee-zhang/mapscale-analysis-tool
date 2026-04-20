import httpx
from ..config import ORS_API_KEY, MODE_MAPPING
from ..errors import ExternalServiceError
from ..observability.logger import get_logger

logger = get_logger("client.ors")


async def fetch_isochrone(lng: float, lat: float, minutes: int, profile: str) -> dict:
    ors_profile = MODE_MAPPING.get(profile, "foot-walking")
    url = f"https://api.openrouteservice.org/v2/isochrones/{ors_profile}"
    body = {"locations": [[lng, lat]], "range": [minutes * 60], "range_type": "time"}

    logger.info("→ isochrone", extra={"ors_profile": ors_profile, "lng": lng, "lat": lat, "minutes": minutes})
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=body, headers={"Authorization": ORS_API_KEY})
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
    start_lng: float, start_lat: float, end_lng: float, end_lat: float, mode: str
) -> dict:
    profile = MODE_MAPPING.get(mode, "foot-walking")
    url = f"https://api.openrouteservice.org/v2/directions/{profile}"
    params = {"api_key": ORS_API_KEY, "start": f"{start_lng},{start_lat}", "end": f"{end_lng},{end_lat}"}

    logger.info("→ directions", extra={"profile": profile})
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
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
