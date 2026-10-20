"""
Seed the `municipality` table from the PDOK CBS WFS (~342 Dutch municipalities).

Usage:
    python scripts/seed_municipalities.py

Idempotent: re-running upserts on the CBS gemeentecode, so it is also how the
table is refreshed after the yearly CBS municipal reorganisation.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from api.main.domains.housing.boundaries import WFS_BASE, WFS_PARAMS  # noqa: E402

# ST_Multi normalises the Polygon/MultiPolygon mix PDOK returns into the single
# MultiPolygon type the column declares.
_UPSERT = """
INSERT INTO municipality (code, name, geom)
VALUES ($1, $2, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON($3), 4326)))
ON CONFLICT (code) DO UPDATE
SET name = EXCLUDED.name, geom = EXCLUDED.geom, fetched_at = now()
"""


async def main() -> None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(WFS_BASE, params=WFS_PARAMS)
        resp.raise_for_status()
        features = resp.json().get("features", [])

    rows = []
    skipped = 0
    for feature in features:
        props = feature.get("properties") or {}
        code = props.get("statcode")
        name = props.get("statnaam") or props.get("gemeentenaam")
        geometry = feature.get("geometry")
        if not code or not name or not geometry:
            skipped += 1
            continue
        rows.append((code, name, json.dumps(geometry)))

    print(f"fetched {len(features)} features, {len(rows)} usable, {skipped} skipped")
    if not rows:
        raise SystemExit("no usable features — refusing to touch the table")

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.executemany(_UPSERT, rows)
        total = await conn.fetchval("SELECT count(*) FROM municipality")
    finally:
        await conn.close()

    print(f"municipality now has {total} rows")


if __name__ == "__main__":
    asyncio.run(main())
