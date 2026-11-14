"""Which municipality a coordinate falls in.

Moved out of routers/region.py unchanged (2026-11-14). The router held the cache key, the
TTL and the call into `upstream/`, while `upstream/pdok.py` held the two domain rules: that
an empty PDOK response means 404, and that a `regionCode` is the CBS code with a `GM`
prefix. Both of those are statements about Dutch municipalities, not about HTTP transport.

The cache key and the 7-day TTL are byte-for-byte what the router used. `region:{lng:.3f}`
is 3 decimals, ~110 m at this latitude, which is deliberate: a municipality boundary does
not move and neighbouring clicks should share an answer. Do not "unify" it with
`domains/geo/service.py`'s `geo_cache_key`, which is 5 decimals because /pois has to read the key /isochrone wrote.
"""
from __future__ import annotations

from fastapi import HTTPException

from ...cache import kv_get, kv_set
from ...logger import get_logger
from ...upstream.pdok import reverse_gemeente

logger = get_logger("domain.region")

# A week. The answer changes when the Netherlands redraws a municipality, which happens on
# 1 January and makes the news.
TTL_SECONDS = 604800


async def get_region(lng: float, lat: float) -> dict:
    cache_key = f"region:{lng:.3f}:{lat:.3f}"
    if cached := await kv_get(cache_key):
        logger.info("cache hit", extra={"cache_key": cache_key})
        return cached

    docs = await reverse_gemeente(lng, lat)
    if not docs:
        raise HTTPException(status_code=404, detail="No municipality found for these coordinates")
    doc = docs[0]
    result = {"name": doc.get("gemeentenaam", ""), "regionCode": f"GM{doc.get('gemeentecode', '')}"}
    logger.info("← region", extra={"result": result})

    await kv_set(cache_key, result, ttl_seconds=TTL_SECONDS)
    return result
