"""Find where this backend stops, and which resource stops it.

    .venv/bin/python scripts/loadtest.py --scenario health
    .venv/bin/python scripts/loadtest.py --scenario permits --vary

Ramps concurrency through a series of steps and reports what each step actually achieved.
Watch http://localhost:3000/d/mapscale-backend while it runs — the point is to read the
throughput knee together with loop lag and pool usage, because the number alone does not
say which wall was hit.

Three deliberate choices:

- **Closed-loop, fixed concurrency per step.** N workers each send one request and wait.
  That measures the server's service rate; an open-loop generator firing at a target rate
  would instead measure how fast a queue can grow once the server is behind.
- **`--vary` moves the coordinate.** `domains/geo` keeps an in-process single-flight map
  in front of the ORS call, keyed on (lng, lat, minutes, profile). Hammering ONE
  coordinate therefore measures the deduplicated path and flatters the result; varying it
  measures the real one. Both are worth knowing, which is why it is a flag and not a
  default either way.
- **Latency is reported from the client.** The server's own histogram excludes time spent
  queued in uvicorn's accept backlog, which is exactly the time that appears first when
  the ceiling is reached.
"""
from __future__ import annotations

import argparse
import asyncio
import multiprocessing as mp
import os
import statistics
import time

import httpx

BASE = "http://localhost:8000/api/main"

# Around Amsterdam, where the data actually is. Varied requests jitter off these.
CENTRES = [(4.9003, 52.3791), (4.8952, 52.3702), (4.9200, 52.3600), (4.8800, 52.3900)]

SCENARIOS = {
    # No DB, no upstream, no cache: the bare FastAPI + uvicorn ceiling on this machine.
    # Everything else has to be read against this number, not against zero.
    "health": lambda i, vary: (f"{BASE}/health", {}),
    # Postgres + an ORS isochrone. The app's normal page load.
    "pois": lambda i, vary: (f"{BASE}/pois", _geo(i, vary, {"minutes": 10, "profile": "walking"})),
    # The heaviest endpoint: same isochrone, then a GiST scan + 5 aggregates over the hit set.
    "permits": lambda i, vary: (
        f"{BASE}/bekendmakingen",
        _geo(i, vary, {"minutes": 10, "profile": "walking", "building_permits": "true"}),
    ),
    # Isochrone only — isolates the ORS leg from the Postgres leg.
    "isochrone": lambda i, vary: (f"{BASE}/isochrone", _geo(i, vary, {"minutes": 10, "profile": "walking"})),
}


def _geo(i: int, vary: bool, extra: dict) -> dict:
    lng, lat = CENTRES[i % len(CENTRES)] if vary else CENTRES[0]
    if vary:
        # ~11 m per 0.0001 deg: enough to be a different cache key and a different
        # isochrone, close enough that the result set stays comparable between steps.
        lng += (i % 97) * 0.0002
        lat += (i % 89) * 0.0002
    return {"lng": round(lng, 6), "lat": round(lat, 6), **extra}


async def _worker(client, scenario, vary, counter, stop_at, results, errors):
    while time.perf_counter() < stop_at:
        i = next(counter)
        url, params = SCENARIOS[scenario](i, vary)
        t0 = time.perf_counter()
        try:
            r = await client.get(url, params=params)
            dt = time.perf_counter() - t0
            (results if r.status_code < 500 else errors).append(dt)
        except Exception:
            errors.append(time.perf_counter() - t0)


def _counter():
    i = 0
    while True:
        yield i
        i += 1


def _pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


async def run_step(scenario: str, vary: bool, concurrency: int, seconds: float) -> dict:
    results: list[float] = []
    errors: list[float] = []
    counter = _counter()
    limits = httpx.Limits(max_connections=concurrency + 10, max_keepalive_connections=concurrency + 10)
    async with httpx.AsyncClient(timeout=120.0, limits=limits) as client:
        stop_at = time.perf_counter() + seconds
        t0 = time.perf_counter()
        await asyncio.gather(*(
            _worker(client, scenario, vary, counter, stop_at, results, errors)
            for _ in range(concurrency)
        ))
        elapsed = time.perf_counter() - t0
    n = len(results) + len(errors)
    return {
        "concurrency": concurrency,
        "rps": n / elapsed if elapsed else 0.0,
        "ok": len(results),
        "err": len(errors),
        "p50": _pct(results, 0.50),
        "p95": _pct(results, 0.95),
        "p99": _pct(results, 0.99),
        "mean": statistics.fmean(results) if results else float("nan"),
    }


def _child(args_tuple):
    """One generator PROCESS. Returns its raw latencies so the parent can merge them.

    Percentiles are computed on the MERGED sample, never averaged across processes —
    an average of per-process p95s is not the p95 of anything.
    """
    base, scenario, url, vary, coros, seconds, offset = args_tuple
    global BASE
    BASE = base
    if url:
        SCENARIOS["__url__"] = lambda i, vary: (url, {})

    async def go():
        results: list[float] = []
        errors: list[float] = []
        counter = _counter_from(offset)
        limits = httpx.Limits(max_connections=coros + 10, max_keepalive_connections=coros + 10)
        async with httpx.AsyncClient(timeout=120.0, limits=limits) as client:
            stop_at = time.perf_counter() + seconds
            t0 = time.perf_counter()
            await asyncio.gather(*(
                _worker(client, scenario, vary, counter, stop_at, results, errors)
                for _ in range(coros)
            ))
            return results, errors, time.perf_counter() - t0

    return asyncio.run(go())


def _counter_from(start: int):
    i = start
    while True:
        yield i
        i += 1


def _run_step_multiproc(args, concurrency: int) -> dict:
    procs = min(args.procs, concurrency)
    per = [concurrency // procs + (1 if i < concurrency % procs else 0) for i in range(procs)]
    scenario = "__url__" if args.url else args.scenario
    # Each process starts its counter in a different range so --vary spreads coordinates
    # across processes instead of every process walking the same sequence.
    payload = [(BASE, scenario, args.url, args.vary, per[i], args.seconds, i * 100_000)
               for i in range(procs)]
    with mp.get_context("spawn").Pool(procs) as pool:
        out = pool.map(_child, payload)
    results = [x for r, _, _ in out for x in r]
    errors = [x for _, e, _ in out for x in e]
    elapsed = max(d for _, _, d in out)
    n = len(results) + len(errors)
    return {
        "concurrency": concurrency, "rps": n / elapsed if elapsed else 0.0,
        "ok": len(results), "err": len(errors),
        "p50": _pct(results, 0.50), "p95": _pct(results, 0.95), "p99": _pct(results, 0.99),
        "mean": statistics.fmean(results) if results else float("nan"),
    }


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="permits")
    ap.add_argument("--steps", default="1,2,4,8,16,32,64",
                    help="concurrency levels, comma separated")
    ap.add_argument("--seconds", type=float, default=15.0, help="duration per step")
    ap.add_argument("--vary", action="store_true",
                    help="move the coordinate per request, defeating the single-flight map")
    ap.add_argument("--url", help=(
        "ignore --scenario and hammer this URL instead. This exists for ONE purpose: "
        "running the generator against a known-fast server (Prometheus's /-/healthy is a "
        "Go binary) to find the generator's OWN ceiling. A single Python process both "
        "generating and timing load is a plausible bottleneck at high concurrency, and "
        "without this control a collapse in the numbers cannot be attributed to the server."))
    ap.add_argument("--base", help="override the API base URL, e.g. a second uvicorn on :8001")
    ap.add_argument("--procs", type=int, default=max(2, (os.cpu_count() or 4) // 2), help=(
        "generator PROCESSES, each running concurrency/procs coroutines. Default is half "
        "the cores. One process was measured topping out near 680 req/s against a Go "
        "server that was not breaking a sweat, so a single-process generator cannot find "
        "this backend's ceiling — it finds its own."))
    args = ap.parse_args()

    global BASE
    if args.base:
        BASE = args.base.rstrip("/")
    if args.url:
        SCENARIOS["__url__"] = lambda i, vary: (args.url, {})
        args.scenario = "__url__"

    steps = [int(s) for s in args.steps.split(",")]
    label = args.url or f"{args.scenario} @ {BASE}"
    print(f"target={label}  vary={args.vary}  {args.seconds:.0f}s per step  "
          f"procs={args.procs}\n")
    print(f"{'conc':>5} {'rps':>8} {'ok':>6} {'err':>5} "
          f"{'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8}")
    rows = []
    for c in steps:
        r = _run_step_multiproc(args, c)
        rows.append(r)
        print(f"{r['concurrency']:5d} {r['rps']:8.1f} {r['ok']:6d} {r['err']:5d} "
              f"{r['p50']*1000:8.1f} {r['p95']*1000:8.1f} {r['p99']*1000:8.1f}", flush=True)
        # Let queues drain and the Grafana panels separate the steps visually.
        await asyncio.sleep(3)

    best = max(rows, key=lambda r: r["rps"])
    print(f"\npeak {best['rps']:.1f} req/s at concurrency {best['concurrency']} "
          f"(p95 {best['p95']*1000:.0f} ms)")
    print("Past the knee, rps flattens while p95 climbs — that is queueing, not capacity.")


if __name__ == "__main__":
    asyncio.run(main())
