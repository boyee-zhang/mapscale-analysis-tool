import json

from . import http_client
from .config import UPSTASH_URL, UPSTASH_TOKEN
from .logger import get_logger

logger = get_logger("cache")


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
