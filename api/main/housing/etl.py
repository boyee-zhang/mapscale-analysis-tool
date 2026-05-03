"""
Housing ETL — snapshots H2S listings into Upstash Redis.

Run manually:
    python -m api.main.housing.etl

Or schedule via cron / Upstash QStash (POST to /api/main/housing/etl/run).

Data model in Redis:
  h2s:listing:{id}   Hash   full listing fields + first_seen, last_seen, booked_at
  h2s:timeline       ZSet   score=first_seen_ts  member=listing_id
  h2s:booked         ZSet   score=booked_at_ts   member=listing_id
  h2s:active         Set    currently live listing IDs (reset each run)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..config import UPSTASH_TOKEN, UPSTASH_URL
from ..logger import get_logger
from . import registry
from . import providers as _  # noqa: F401 — triggers provider self-registration
from .upstash import UpstashClient

logger = get_logger("housing.etl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _listing_to_hash(listing) -> dict:
    return {
        "id":             listing.id,
        "name":           listing.name or "",
        "city":           listing.city or "",
        "neighborhood":   listing.neighborhood or "",
        "price":          str(listing.price) if listing.price is not None else "",
        "area_m2":        str(listing.area_m2) if listing.area_m2 is not None else "",
        "property_type":  listing.property_type or "",
        "status":         listing.status or "unknown",
        "available_from": listing.available_from or "",
        "url":            listing.url or "",
        "provider":       listing.provider,
    }


async def run(provider_id: str = "h2s") -> dict:
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        raise RuntimeError("UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN not configured")

    redis = UpstashClient(UPSTASH_URL, UPSTASH_TOKEN)
    provider = registry.get(provider_id)
    if provider is None:
        raise ValueError(f"Provider '{provider_id}' not registered")

    logger.info("etl start", extra={"provider": provider_id})

    # 1. Fetch live listings
    listings = await provider.fetch_listings()
    new_ids = {l.id for l in listings}
    logger.info("etl fetched", extra={"count": len(listings)})

    # 2. Get currently active IDs from Redis
    active_raw = await redis.execute("SMEMBERS", "h2s:active")
    active_ids: set[str] = set(active_raw) if active_raw else set()

    # 3. Disappeared listings → mark booked
    disappeared = active_ids - new_ids
    booked_at = _now_iso()
    booked_ts = _now_ts()
    booked_cmds = []
    for lid in disappeared:
        booked_cmds += [
            ["HSET", f"h2s:listing:{lid}", "status", "booked", "booked_at", booked_at],
            ["ZADD", "h2s:booked", str(booked_ts), lid],
        ]
    if booked_cmds:
        await redis.pipeline(booked_cmds)
        logger.info("etl booked", extra={"count": len(disappeared)})

    # 4. Only write new listings (existing + unchanged ones are skipped)
    now_iso = _now_iso()
    now_ts = _now_ts()
    truly_new = new_ids - active_ids

    new_cmds = []
    for listing in listings:
        if listing.id not in truly_new:
            continue
        key = f"h2s:listing:{listing.id}"
        fields = _listing_to_hash(listing)
        fields["first_seen"] = now_iso
        fields["last_seen"] = now_iso
        hset_cmd = ["HSET", key]
        for k, v in fields.items():
            hset_cmd += [k, v]
        new_cmds.append(hset_cmd)
        new_cmds.append(["ZADD", "h2s:timeline", str(now_ts), listing.id])

    if new_cmds:
        await redis.pipeline(new_cmds)
        logger.info("etl new listings", extra={"count": len(truly_new)})

    # 5. Reset active set
    reset_cmds: list[list] = [["DEL", "h2s:active"]]
    if new_ids:
        reset_cmds.append(["SADD", "h2s:active"] + list(new_ids))
    await redis.pipeline(reset_cmds)

    result = {
        "fetched": len(listings),
        "newly_booked": len(disappeared),
        "new_listings": len(truly_new),
    }
    logger.info("etl done", extra=result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(run()))
