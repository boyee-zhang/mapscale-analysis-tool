"""Re-time the /search/notices ranking query, both branches, 2026-11-14 (falsifier, claim 23)."""
import asyncio, statistics, sys, time
from datetime import date
sys.path.insert(0, "/Users/zby/dev/mapscale-analysis-tool")
from dotenv import load_dotenv
load_dotenv("/Users/zby/dev/mapscale-analysis-tool/.env")
from api.main import db
from api.main.domains.search import encoder, service

CAP = {}
async def _capture(conn, sql, args, spatial):
    CAP.setdefault("calls", []).append((sql, args, spatial))
    return []

async def capture(spatial, limit=20):
    CAP.clear()
    real, service._fetch = service._fetch, _capture
    try:
        kw = {"lng": 4.9003, "lat": 52.3791, "minutes": 10} if spatial else {}
        await service.search(q="附近有什么会吵的工程", date_from=date(2026, 1, 1),
                             date_to=date(2026, 12, 31), limit=limit, **kw)
    finally:
        service._fetch = real
    return CAP["calls"][0]

async def main():
    await db.connect()
    assert encoder.warm()
    try:
        async with db.acquire() as conn:
            for label, spatial in (("corpus", False), ("isochrone", True)):
                sql, args, _ = await capture(spatial)
                t0 = time.perf_counter(); rows = await conn.fetch(sql, *args)
                cold = (time.perf_counter() - t0) * 1000
                ms = []
                for _ in range(24):
                    t0 = time.perf_counter(); await conn.fetch(sql, *args)
                    ms.append((time.perf_counter() - t0) * 1000)
                ms.sort()
                print(f"{label:10} rows={len(rows):3} cold={cold:8.2f}  warm n=24: "
                      f"min={min(ms):7.2f} med={statistics.median(ms):7.2f} "
                      f"p95={ms[int(.95*24)-1]:7.2f} max={max(ms):7.2f}")
    finally:
        await db.close()

asyncio.run(main())
