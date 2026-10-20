"""Upstash Redis REST client — used by the housing ETL and router."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from typing import Any


class UpstashClient:
    def __init__(self, url: str, token: str):
        self._url = url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def pipeline(self, commands: list[list]) -> list[Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._url}/pipeline",
                headers=self._headers,
                json=commands,
                timeout=30,
            )
            resp.raise_for_status()
            return [item["result"] for item in resp.json()]

    async def execute(self, *command) -> Any:
        results = await self.pipeline([list(command)])
        return results[0]

    async def get_past_city_counts(self, months: int = 3) -> dict[str, int]:
        """Return city → newly-listed count for the past N months (first_seen window)."""
        now = datetime.now(timezone.utc)
        end_ts   = int(now.timestamp())
        start_ts = int((now - timedelta(days=30 * months)).timestamp())

        [ids] = await self.pipeline([
            ["ZRANGEBYSCORE", "h2s:timeline", str(start_ts), str(end_ts)],
        ])
        if not ids:
            return {}

        hget_cmds = [["HGETALL", f"h2s:listing:{lid}"] for lid in ids]
        hashes = await self.pipeline(hget_cmds)

        counts: dict[str, int] = {}
        for flat in hashes:
            if not flat:
                continue
            # HGETALL returns [k, v, k, v, ...]
            obj = dict(zip(flat[::2], flat[1::2]))
            city = obj.get("city")
            if city:
                counts[city] = counts.get(city, 0) + 1
        return counts

    async def get_new_listings(self, hours: int = 24) -> list[dict]:
        """Return listings that first appeared within the last N hours."""
        now = datetime.now(timezone.utc)
        end_ts   = int(now.timestamp())
        start_ts = int((now - timedelta(hours=hours)).timestamp())

        [ids] = await self.pipeline([
            ["ZRANGEBYSCORE", "h2s:timeline", str(start_ts), str(end_ts)],
        ])
        if not ids:
            return []

        hget_cmds = [["HGETALL", f"h2s:listing:{lid}"] for lid in ids]
        hashes = await self.pipeline(hget_cmds)

        listings = []
        for flat in hashes:
            if not flat:
                continue
            obj = dict(zip(flat[::2], flat[1::2]))
            if obj.get("id"):
                listings.append(obj)
        return listings

    async def get_active_city_stats(self, city: str) -> dict:
        """Return active listing count and price stats for a city."""
        active_ids = await self.execute("SMEMBERS", "h2s:active")
        if not active_ids:
            return {"city": city, "active_listings": 0, "avg_price": None}

        hget_cmds = [["HGETALL", f"h2s:listing:{lid}"] for lid in active_ids]
        hashes = await self.pipeline(hget_cmds)

        prices = []
        for flat in hashes:
            if not flat:
                continue
            obj = dict(zip(flat[::2], flat[1::2]))
            if obj.get("city") == city and obj.get("price"):
                try:
                    prices.append(float(obj["price"]))
                except ValueError:
                    pass

        if not prices:
            return {"city": city, "active_listings": 0, "avg_price": None}

        return {
            "city": city,
            "active_listings": len(prices),
            "avg_price": round(sum(prices) / len(prices)),
            "min_price": round(min(prices)),
            "max_price": round(max(prices)),
        }
