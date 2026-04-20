import httpx
from fastapi import HTTPException
from ..errors import ExternalServiceError
from ..observability.logger import get_logger

logger = get_logger("client.pdok")

PDOK_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/reverse"


async def fetch_region(lng: float, lat: float) -> dict:
    params = {"lat": lat, "lon": lng, "type": "gemeente", "fl": "gemeentecode,gemeentenaam,id"}
    logger.info("→ region", extra={"lng": lng, "lat": lat})
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(PDOK_URL, params=params)
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
            if not docs:
                raise HTTPException(status_code=404, detail="No municipality found for these coordinates")
            doc = docs[0]
            result = {"name": doc.get("gemeentenaam", ""), "regionCode": f"GM{doc.get('gemeentecode', '')}"}
            logger.info("← region", extra={"result": result})
            return result
    except HTTPException:
        raise
    except httpx.TimeoutException:
        logger.warning("region timeout", extra={"service": "pdok"})
        raise ExternalServiceError("pdok", 504, "request timed out")
    except httpx.HTTPStatusError as e:
        logger.warning("region http error", extra={"service": "pdok", "upstream_status": e.response.status_code})
        raise ExternalServiceError("pdok", e.response.status_code, e.response.text[:200])
    except httpx.RequestError as e:
        logger.warning("region network error", extra={"service": "pdok", "error": str(e)})
        raise ExternalServiceError("pdok", 502, f"network error: {e}")
