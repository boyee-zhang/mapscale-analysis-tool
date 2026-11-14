import httpx
from .. import http_client
from ..errors import ExternalServiceError
from ..logger import get_logger

logger = get_logger("client.pdok")

PDOK_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/reverse"
PDOK_FREE = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"

# CBS gebiedsindelingen WFS — municipality polygons. Used by etl/ingest/municipalities.py.
# These lived in domains/housing/boundaries.py until that domain was removed (2026-10-28);
# they are pure transport config, so upstream/ is where they belong.
WFS_BASE = "https://service.pdok.nl/cbs/gebiedsindelingen/2024/wfs/v1_0"

# One request returns all ~342 municipalities (~300 KB). PDOK's CQL_FILTER is silently
# ignored by this service, so any narrowing has to happen in Python.
WFS_PARAMS = {
    "service":      "WFS",
    "version":      "2.0.0",
    "request":      "GetFeature",
    "typeNames":    "gemeente_gegeneraliseerd",
    "outputFormat": "application/json",
    "count":        "500",
    "srsName":      "EPSG:4326",
}


async def reverse_gemeente(lng: float, lat: float) -> list[dict]:
    """Reverse-geocode a coordinate to PDOK's municipality docs, as PDOK returns them.

    Transport only: the empty-result rule ("no municipality here" -> 404) and the `GM` prefix
    are domain facts and live in domains/region/service.py. Returning [] rather than raising on
    an empty response is the point -- an HTTPException from a transport module is a layer
    violation that this module used to commit at exactly this line.
    """
    params = {"lat": lat, "lon": lng, "type": "gemeente", "fl": "gemeentecode,gemeentenaam,id"}
    try:
        resp = await http_client.get().get(PDOK_URL, params=params, timeout=10.0)
        resp.raise_for_status()
        return resp.json().get("response", {}).get("docs", [])
    except httpx.TimeoutException:
        logger.warning("region timeout", extra={"service": "pdok"})
        raise ExternalServiceError("pdok", 504, "request timed out")
    except httpx.HTTPStatusError as e:
        logger.warning("region http error", extra={"service": "pdok", "upstream_status": e.response.status_code})
        raise ExternalServiceError("pdok", e.response.status_code, e.response.text[:200])
    except httpx.RequestError as e:
        logger.warning("region network error", extra={"service": "pdok", "error": str(e)})
        raise ExternalServiceError("pdok", 502, f"network error: {e}")


async def search_addresses(q: str, size: int) -> list[dict]:
    """Free-text address search against the Locatieserver, as PDOK returns it.

    Split out of routers/search.py (2026-11-14), where the URL sat in the router while this
    module held the reverse-geocode URL for the same service. The caller parses `centroide_ll`
    and maps the Dutch field names -- both are domain work, and both are in
    domains/address/service.py.
    """
    params = {"q": q, "fq": "type:adres", "rows": size, "fl": "*"}
    try:
        resp = await http_client.get().get(PDOK_FREE, params=params, timeout=8.0)
        resp.raise_for_status()
        return resp.json().get("response", {}).get("docs", [])
    except httpx.TimeoutException:
        logger.warning("address search timeout", extra={"service": "pdok"})
        raise ExternalServiceError("pdok", 504, "request timed out")
    except httpx.HTTPStatusError as e:
        logger.warning("address search http error",
                       extra={"service": "pdok", "upstream_status": e.response.status_code})
        raise ExternalServiceError("pdok", e.response.status_code, e.response.text[:200])
    except httpx.RequestError as e:
        logger.warning("address search network error", extra={"service": "pdok", "error": str(e)})
        raise ExternalServiceError("pdok", 502, f"network error: {e}")
