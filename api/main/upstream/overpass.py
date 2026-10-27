import httpx
from ..config import SPEED_MAP
from ..errors import ExternalServiceError
from ..logger import get_logger

logger = get_logger("client.overpass")

# Ordered by MEASURED health, and each mirror carries its own timeout. Probed 2026-10-23
# from the dev machine with the same query this module sends:
#
#   overpass.openstreetmap.fr   200, but 1.2 / 14.4 / 6.0 / 8.0 s across four runs
#   overpass.kumi.systems       no response in 25 s
#   overpass-api.de             no response in 25 s
#
# Two things that single numbers would hide:
#
# 1. The two silent mirrors are neither down nor blocking us. `GET /` on kumi.systems answers
#    200 in 0.16 s and overpass-api.de's /api/status answers in 0.13 s; it is specifically
#    their /api/interpreter POST that never returns. A trivial `out count;` over a
#    0.0001-degree bbox hangs the same way, so it is not query weight either.
#
# 2. The healthy mirror's latency STRADDLES the 8 s timeout this module used to apply to all
#    three. The first probe said 1.24 s; three more said 14.4, 6.0 and 8.0. Reordering alone
#    would therefore still have failed roughly as often as it succeeded, which is why the
#    timeout is now per-mirror rather than a flat 8 s: give the one that answers enough room
#    (18 s covers the slowest observed run), and let the two that never answer fail fast
#    (4 s) instead of spending 8 s each to learn what we already measured.
#
# Worst case is now 18 + 4 + 4 = 26 s, which still fits inside the 30 s client timeout in
# tests/integration/test_pois.py.
#
# This is a snapshot of one day, not a ranking. RE-MEASURE before trusting it, and move a
# mirror back up with a longer budget if it recovers.
OVERPASS_ENDPOINTS = [
    ("https://overpass.openstreetmap.fr/api/interpreter", 18.0),
    ("https://overpass.kumi.systems/api/interpreter", 4.0),
    ("https://overpass-api.de/api/interpreter", 4.0),
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
    for url, timeout_s in OVERPASS_ENDPOINTS:
        try:
            async with httpx.AsyncClient(timeout=timeout_s, headers=_HEADERS) as client:
                resp = await client.post(url, data={"data": query})
                resp.raise_for_status()
                data = resp.json()
                logger.info("← pois", extra={"elements": len(data.get("elements", [])), "endpoint": url})
                return data
        except httpx.TimeoutException:
            logger.warning("pois timeout", extra={"service": "overpass", "endpoint": url, "timeout_s": timeout_s})
            last_error = ExternalServiceError("overpass", 504, "query timed out — try a smaller area")
        except httpx.HTTPStatusError as e:
            logger.warning("pois http error", extra={"service": "overpass", "endpoint": url, "upstream_status": e.response.status_code})
            last_error = ExternalServiceError("overpass", e.response.status_code, "overpass rejected query")
        except httpx.RequestError as e:
            logger.warning("pois network error", extra={"service": "overpass", "endpoint": url, "error": str(e)})
            last_error = ExternalServiceError("overpass", 502, f"network error: {e}")

    raise last_error
