import json
import httpx
from .config import UPSTASH_URL, UPSTASH_TOKEN


async def kv_get(key: str):
    """Return parsed JSON value from KV, or None if missing / KV not configured."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{UPSTASH_URL}/get/{key}",
                headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            )
            result = resp.json().get("result")
            return json.loads(result) if result else None
    except Exception as e:
        print(f"[cache] GET {key} failed: {e}")
        return None


async def kv_set(key: str, value, ttl_seconds: int):
    """Store JSON value in KV with TTL. Fire-and-forget."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(
                f"{UPSTASH_URL}/set/{key}",
                headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                json={"value": json.dumps(value), "ex": ttl_seconds},
            )
    except Exception as e:
        print(f"[cache] SET {key} failed: {e}")
