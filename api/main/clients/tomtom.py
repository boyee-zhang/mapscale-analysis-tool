import httpx
from ..config import TOMTOM_API_KEY
from ..errors import ExternalServiceError
from ..observability.logger import get_logger

logger = get_logger("client.tomtom")

FLOW_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
INCIDENTS_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"


async def fetch_flow(lat: float, lng: float) -> dict:
    """
    Returns current speed vs free-flow speed for the road segment nearest to the given point.
    Response includes: currentSpeed, freeFlowSpeed, currentTravelTime, freeFlowTravelTime, confidence
    """
    params = {"point": f"{lat},{lng}", "key": TOMTOM_API_KEY}
    logger.info("→ flow", extra={"lat": lat, "lng": lng})
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(FLOW_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            flow = data.get("flowSegmentData", {})
            logger.info("← flow", extra={
                "currentSpeed": flow.get("currentSpeed"),
                "freeFlowSpeed": flow.get("freeFlowSpeed"),
            })
            return flow
    except httpx.TimeoutException:
        raise ExternalServiceError("tomtom", 504, "flow request timed out")
    except httpx.HTTPStatusError as e:
        raise ExternalServiceError("tomtom", e.response.status_code, e.response.text[:200])
    except httpx.RequestError as e:
        raise ExternalServiceError("tomtom", 502, f"network error: {e}")


async def fetch_incidents(min_lng: float, min_lat: float, max_lng: float, max_lat: float) -> list:
    """
    Returns traffic incidents (accidents, construction, closures) within the bounding box.
    Each incident has: type, geometry (point), description, severity (0-4), delay
    """
    params = {
        "bbox": f"{min_lng},{min_lat},{max_lng},{max_lat}",
        "fields": "{incidents{type,geometry{coordinates},properties{iconCategory,startTime,endTime,from,to,length,delay,roadNumbers,timeValidity}}}",
        "language": "en-GB",
        "key": TOMTOM_API_KEY,
    }
    logger.info("→ incidents", extra={"bbox": params["bbox"]})
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(INCIDENTS_URL, params=params)
            resp.raise_for_status()
            incidents = resp.json().get("incidents", [])
            logger.info("← incidents", extra={"count": len(incidents)})
            return incidents
    except httpx.TimeoutException:
        raise ExternalServiceError("tomtom", 504, "incidents request timed out")
    except httpx.HTTPStatusError as e:
        raise ExternalServiceError("tomtom", e.response.status_code, e.response.text[:200])
    except httpx.RequestError as e:
        raise ExternalServiceError("tomtom", 502, f"network error: {e}")
