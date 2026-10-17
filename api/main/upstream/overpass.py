import httpx
from ..config import SPEED_MAP
from ..errors import ExternalServiceError
from ..logger import get_logger

logger = get_logger("client.overpass")

OVERPASS_ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]

_HEADERS = {"User-Agent": "mapscale/1.0 (https://github.com/mapscale)"}


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

    last_error = None
    for url in OVERPASS_ENDPOINTS:
        try:
            async with httpx.AsyncClient(timeout=8.0, headers=_HEADERS) as client:
                resp = await client.post(url, data={"data": query})
                resp.raise_for_status()
                data = resp.json()
                logger.info("← pois", extra={"elements": len(data.get("elements", [])), "endpoint": url})
                return data
        except httpx.TimeoutException:
            logger.warning("pois timeout", extra={"service": "overpass", "endpoint": url})
            last_error = ExternalServiceError("overpass", 504, "query timed out — try a smaller area")
        except httpx.HTTPStatusError as e:
            logger.warning("pois http error", extra={"service": "overpass", "endpoint": url, "upstream_status": e.response.status_code})
            last_error = ExternalServiceError("overpass", e.response.status_code, "overpass rejected query")
        except httpx.RequestError as e:
            logger.warning("pois network error", extra={"service": "overpass", "endpoint": url, "error": str(e)})
            last_error = ExternalServiceError("overpass", 502, f"network error: {e}")

    raise last_error
