"""Falsifier harness for claim 17 (/search/notices ranking-query cost), 2026-11-14.

Read-only. Captures the SQL the real code path builds (by wrapping service._fetch), then:
  1 re-runs the as-shipped query 5x through the pool
  2 runs a scalar-subquery rewrite that probes bekendmaking_activity for the surviving rows only
  3 compares the two row sets
  4 repeats the measurement on the SPATIAL branch (the axis the claim did not vary)
  5 sweeps limit / doc_type / window / query language
  6 checks whether the corpus branch under-returns without hnsw.iterative_scan

    .venv/bin/python docs/pg-notes/scripts/notices_ranking_falsify.py
"""
import asyncio, sys, time
from datetime import date

sys.path.insert(0, "/Users/zby/dev/mapscale-analysis-tool")
from dotenv import load_dotenv
load_dotenv("/Users/zby/dev/mapscale-analysis-tool/.env")
from api.main import db                                        # noqa: E402
from api.main.domains.search import encoder, service           # noqa: E402

Q = "附近有什么会吵的工程"
CAP = []
_fetch = service._fetch


async def grab(conn, sql, args, spatial):
    CAP.append((sql, args, spatial))
    return await _fetch(conn, sql, args, spatial)


service._fetch = grab


def rewrite(sql):
    """RANK FIRST, THEN DECORATE -- as a scalar subquery instead of a join."""
    return (sql.replace("JOIN bekendmaking_activity a USING (identifier)", "")
               .replace("a.activity_text,",
                        "(SELECT a2.activity_text FROM bekendmaking_activity a2 "
                        "WHERE a2.identifier = r.identifier) AS activity_text,"))


async def times(conn, sql, args, n=5):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        rows = await conn.fetch(sql, *args)
        out.append((time.perf_counter() - t) * 1000)
    return out, len(rows)


async def main():
    await db.connect()
    encoder.warm()
    CAP.clear()
    await service.search(q=Q, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31), limit=20)
    sql, args, spatial = CAP[-1]
    assert not spatial
    async with db.acquire() as conn:
        print("plan_cache_mode on a pooled connection:", await conn.fetchval("show plan_cache_mode"))
        t, n = await times(conn, sql, args)
        print(f"[1] as shipped        rows={n} ms={[f'{x:.0f}' for x in t]}")
        t, n = await times(conn, rewrite(sql), args)
        print(f"[2] scalar subquery   rows={n} ms={[f'{x:.1f}' for x in t]}")
        A = [dict(r) for r in await conn.fetch(sql, *args)]
        B = [dict(r) for r in await conn.fetch(rewrite(sql), *args)]
        print(f"[3] same id set={ {a['identifier'] for a in A} == {b['identifier'] for b in B} }  "
              f"same order={[a['identifier'] for a in A] == [b['identifier'] for b in B]}  "
              f"ties={sum(1 for i in range(1, len(A)) if A[i]['distance'] == A[i-1]['distance'])}")

    CAP.clear()
    r = await service.search(q=Q, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31),
                             lng=4.9003, lat=52.3791, minutes=10, profile="walking", limit=20)
    ssql, sargs, _ = CAP[0]
    async with db.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
        t, n = await times(conn, ssql, sargs, 3)
        print(f"[4] spatial branch    shape={r['shape']} rows={n} ms={[f'{x:.0f}' for x in t]}")

    cases = [("limit=1", dict(limit=1)), ("limit=5", dict(limit=5)), ("limit=20", dict(limit=20)),
             ("limit=50", dict(limit=50)),
             ("doc_type=evenementenvergunning", dict(limit=20, doc_type=["evenementenvergunning"])),
             ("window=1 week", dict(limit=20, date_from=date(2026, 6, 1), date_to=date(2026, 6, 7))),
             ("query=muziekfestival (nl)", dict(limit=20, q="muziekfestival"))]
    for label, kw in cases:
        kw = {"q": Q, "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31), **kw}
        CAP.clear()
        await service.search(**kw)
        s, a, _ = CAP[-1]
        async with db.acquire() as conn:
            t, n = await times(conn, s, a, 3)
            plan = "\n".join(x[0] for x in await conn.fetch(
                "EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF) " + s, *a))
            buf = next(l.strip() for l in plan.splitlines() if "Buffers: shared" in l)
            print(f"[5] {label:32s} rows={n:2d} ms={[f'{x:.0f}' for x in t]} "
                  f"body_seq_scan={'Seq Scan on bekendmaking_body' in plan} {buf}")
            async with conn.transaction():
                await conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
                _, ni = await times(conn, s, a, 1)
            print(f"[6] {label:32s} default={n:2d} iterative_scan={ni:2d}")
    await db.close()


asyncio.run(main())
