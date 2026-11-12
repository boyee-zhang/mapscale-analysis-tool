import json

from . import http_client
from .config import UPSTASH_URL, UPSTASH_TOKEN
from .logger import get_logger

logger = get_logger("cache")


def geo_cache_key(prefix: str, lng: float, lat: float, minutes: int, profile: str) -> str:
    """Cache key for the (lng, lat, minutes, profile) request shape.

    One helper for every prefix that uses this shape, because /pois reads the
    isochrone that /isochrone wrote and the two keys have to agree to the last
    decimal. They did not: `iso:` was formatted with 5 decimals and `pois:` with 3,
    so reading one from the other would have missed every single time.
    """
    return f"{prefix}:{lng:.5f}:{lat:.5f}:{minutes}:{profile}"


async def kv_get(key: str):
    """Return parsed JSON value from KV, or None if missing / KV not configured."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return None
    try:
        resp = await http_client.get().get(
            f"{UPSTASH_URL}/get/{key}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=3.0,
        )
        result = resp.json().get("result")
        return json.loads(result) if result else None
    except Exception as e:
        logger.warning("kv get failed", extra={"key": key, "error": str(e)})
        return None


async def kv_set(key: str, value, ttl_seconds: int):
    """Store JSON value in KV with TTL. Fire-and-forget."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return
    try:
        await http_client.get().post(
            f"{UPSTASH_URL}/set/{key}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            json={"value": json.dumps(value), "ex": ttl_seconds},
            timeout=3.0,
        )
    except Exception as e:
        logger.warning("kv set failed", extra={"key": key, "error": str(e)})
