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
        """Return city → booked-listing count for the past N months."""
        now = datetime.now(timezone.utc)
        end_ts   = int(now.timestamp())
        start_ts = int((now - timedelta(days=30 * months)).timestamp())

        [ids] = await self.pipeline([
            ["ZRANGEBYSCORE", "h2s:booked", str(start_ts), str(end_ts)],
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
