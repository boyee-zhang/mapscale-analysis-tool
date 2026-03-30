import httpx
from ..config import SPEED_MAP
from ..errors import ExternalServiceError
from ..observability.logger import get_logger

logger = get_logger("client.overpass")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


async def fetch_pois(lng: float, lat: float, minutes: int, profile: str) -> dict:
    radius = minutes * SPEED_MAP.get(profile, 80)
    query = f"""
    [out:json];
    (
      node["shop"~"supermarket|convenience"](around:{radius}, {lat}, {lng});
      node["leisure"="fitness_centre"](around:{radius}, {lat}, {lng});
      node["amenity"="gym"](around:{radius}, {lat}, {lng});
    );
    out body;
    """
    logger.info("→ pois", extra={"radius_m": radius, "lng": lng, "lat": lat})
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(OVERPASS_URL, data={"data": query})
            resp.raise_for_status()
            data = resp.json()
            logger.info("← pois", extra={"elements": len(data.get("elements", []))})
            return data
    except httpx.ReadTimeout:
        logger.warning("pois timeout", extra={"service": "overpass"})
        raise ExternalServiceError("overpass", 504, "query timed out — try a smaller area")
    except httpx.HTTPStatusError as e:
        logger.warning("pois http error", extra={"service": "overpass", "upstream_status": e.response.status_code})
        raise ExternalServiceError("overpass", e.response.status_code, "overpass rejected query")
    except httpx.RequestError as e:
        logger.warning("pois network error", extra={"service": "overpass", "error": str(e)})
        raise ExternalServiceError("overpass", 502, f"network error: {e}")
