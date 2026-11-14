"""Find where this backend stops, and which resource stops it.

    .venv/bin/python tools/loadtest.py --scenario health
    .venv/bin/python tools/loadtest.py --scenario permits --vary

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
    # --- LOCAL: served entirely by this machine (Postgres + the self-hosted ORS on :8080).
    # Safe to saturate; these are the only ones whose ceiling is OUR backend's.
    #
    # No DB, no upstream, no cache: the bare FastAPI + uvicorn ceiling on this machine.
    # Everything else has to be read against this number, not against zero.
    "health": lambda i, vary: (f"{BASE}/health", {}),
    # Also no upstream — it only formats a URL string. Separates "a trivial handler" from
    # "/health", which FastAPI may treat differently, at no cost.
    "tile_url": lambda i, vary: (f"{BASE}/traffic/tile-url", {}),
    # ORS only, no Postgres: isolates the routing leg.
    "isochrone": lambda i, vary: (f"{BASE}/isochrone", _geo(i, vary, {"minutes": 10, "profile": "walking"})),
    # Postgres + an ORS isochrone. The app's normal page load.
    "pois": lambda i, vary: (f"{BASE}/pois", _geo(i, vary, {"minutes": 10, "profile": "walking"})),
    # The heaviest endpoint: same isochrone, then a GiST scan + 5 aggregates over the hit set.
    "permits": lambda i, vary: (
        f"{BASE}/bekendmakingen",
        _geo(i, vary, {"minutes": 10, "profile": "walking", "building_permits": "true"}),
    ),
    # One ORS route. No frontend caller today, but it is exposed, so it is measured.
    "directions": lambda i, vary: (f"{BASE}/directions", _route(i, vary)),
    # The semantic endpoint, and until 2026-11-14 the only one never measured here -- which
    # is why a torch forward pass sat on the event loop and a ~2 s ranking query went
    # unnoticed. Local: the encoder runs in-process and pgvector is our own Postgres, so
    # this is NOT third party and may be saturated. It is slow; use a short --seconds.
    "notices": lambda i, vary: (f"{BASE}/search/notices", _notices(i, vary)),

    # --- THIRD PARTY: every request leaves this machine for a service we do not own.
    # Saturating these measures THEIR rate limiter and burns quota or abuses a free
    # public service. Guarded below; use --probe to time them instead.
    "region": lambda i, vary: (f"{BASE}/region", _geo(i, vary, {})),
    "search": lambda i, vary: (f"{BASE}/search", {"q": _QUERIES[i % len(_QUERIES)]}),
    "traffic_flow": lambda i, vary: (f"{BASE}/traffic/flow", _geo(i, vary, {})),
    "traffic_incidents": lambda i, vary: (f"{BASE}/traffic/incidents", {
        "min_lng": 4.85, "min_lat": 52.35, "max_lng": 4.95, "max_lat": 52.40}),
}

# Which scenarios may be driven to saturation. This is a property of where the work
# happens, not a preference: the four below leave the machine on every request.
#   region            -> PDOK, a Dutch government open service
#   search            -> ES (down here) -> PDOK -> Nominatim, whose usage policy is 1 req/s
#   traffic_flow      -> TomTom, a metered key
#   traffic_incidents -> TomTom, a metered key
# Encoded here rather than written in a doc, because a note does not stop a command.
THIRD_PARTY = {"region", "search", "traffic_flow", "traffic_incidents"}
LOCAL = [k for k in SCENARIOS if k not in THIRD_PARTY]

_QUERIES = ["Damrak 1", "1012 Amsterdam", "Utrecht Centraal", "Haarlemmerstraat",
            "Prinsengracht 263", "Zuidas", "Leidseplein", "Vondelpark"]


def _route(i: int, vary: bool) -> dict:
    """A short walkable route, both ends inside the Noord-Holland graph."""
    lng, lat = CENTRES[i % len(CENTRES)] if vary else CENTRES[0]
    d = (i % 23) * 0.0004 if vary else 0.0
    return {"start_lng": round(lng, 6), "start_lat": round(lat, 6),
            "end_lng": round(lng + 0.004 + d, 6), "end_lat": round(lat + 0.003, 6),
            "mode": "walking"}



def _notices(i: int, vary: bool) -> dict:
    """Rotate real resident questions, so the encoder sees distinct text and the vector
    sort cannot be answered from a repeated plan."""
    qs = ["附近有什么会吵的工程", "我家附近有人要砍树吗", "附近最近要办什么活动吗",
          "哪条路要封闭施工", "有人申请夜间施工许可吗"]
    return {"q": qs[i % len(qs)] if vary else qs[0], "limit": 20}


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
            # `< 400`, not `< 500`. This counted 404 as a success, and a wrong --base
            # (below) pointed every request at a path that does not exist: 82,110 404s
            # in 30 s reported as `err 0` and 2736 rps, which is ~8x the real ceiling of
            # the endpoint it claimed to be measuring. A load generator that cannot tell
            # "served" from "routed nowhere" invents capacity out of a typo.
            (results if r.status_code < 400 else errors).append(dt)
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
    ap.add_argument("--scenario", choices=sorted(SCENARIOS) + ["all"], default="permits",
                    help="one scenario, or `all` to sweep every LOCAL one in sequence")
    ap.add_argument("--probe", type=int, metavar="N", help=(
        "do not load-test: send N requests one at a time and report latency. This is the "
        "ONLY supported way to measure a THIRD_PARTY scenario, because its ceiling is the "
        "upstream's rate limiter, not this backend's capacity."))
    ap.add_argument("--force-thirdparty", action="store_true", help=(
        "saturate a THIRD_PARTY scenario anyway. Doing so burns a metered quota (TomTom) "
        "or abuses a free public service (PDOK, Nominatim at 1 req/s). Off by default."))
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
    ap.add_argument("--base", help="override the API origin, e.g. http://127.0.0.1:8001 for a second uvicorn. `/api/main` is appended if absent.")
    ap.add_argument("--procs", type=int, default=max(2, (os.cpu_count() or 4) // 2), help=(
        "generator PROCESSES, each running concurrency/procs coroutines. Default is half "
        "the cores. One process was measured topping out near 680 req/s against a Go "
        "server that was not breaking a sweat, so a single-process generator cannot find "
        "this backend's ceiling — it finds its own."))
    args = ap.parse_args()

    global BASE
    if args.base:
        # Append the API prefix when it is missing. BASE carries `/api/main`, so a bare
        # origin (the obvious thing to pass, and what the old help text invited) silently
        # produced 404s for every scenario.
        BASE = args.base.rstrip("/")
        if not BASE.endswith("/api/main"):
            BASE += "/api/main"
    if args.url:
        SCENARIOS["__url__"] = lambda i, vary: (args.url, {})
        args.scenario = "__url__"

    if args.probe:
        await _probe(args.scenario, args.probe, args.vary)
        return

    if args.scenario in THIRD_PARTY and not args.force_thirdparty:
        ap.error(
            f"'{args.scenario}' leaves this machine on every request. Saturating it measures "
            f"the upstream's rate limiter and burns quota or abuses a free service. Use "
            f"--probe N to time it, or --force-thirdparty if you really mean it.")

    if args.scenario == "all":
        for name in LOCAL:
            args.scenario = name
            await _sweep(args, [int(x) for x in args.steps.split(",")])
            print()
        return

    await _sweep(args, [int(x) for x in args.steps.split(",")])


async def _sweep(args, steps: list[int]) -> dict:
    label = args.url or f"{args.scenario} @ {BASE}"
    print(f"target={label}  vary={args.vary}  {args.seconds:.0f}s per step  "
          f"procs={args.procs}")
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
    print(f"peak {best['rps']:.1f} req/s at concurrency {best['concurrency']} "
          f"(p95 {best['p95']*1000:.0f} ms, err {best['err']})")
    return best


async def _probe(scenario: str, n: int, vary: bool) -> None:
    """Sequential, one request at a time. For endpoints whose ceiling is not ours."""
    counter = _counter()
    lat: list[float] = []
    codes: dict[int, int] = {}
    print(f"probe {scenario} @ {BASE}  n={n}  (sequential, not a load test)")
    async with httpx.AsyncClient(timeout=60.0) as client:
        for _ in range(n):
            url, params = SCENARIOS[scenario](next(counter), vary)
            t0 = time.perf_counter()
            try:
                r = await client.get(url, params=params)
                lat.append(time.perf_counter() - t0)
                codes[r.status_code] = codes.get(r.status_code, 0) + 1
            except Exception as e:
                codes[0] = codes.get(0, 0) + 1
                print(f"  request failed: {type(e).__name__}: {e}")
    if lat:
        print(f"  n={len(lat)}  min {min(lat)*1000:.0f} ms  median {_pct(lat, .5)*1000:.0f} ms  "
              f"max {max(lat)*1000:.0f} ms  -> ~{1/statistics.fmean(lat):.1f} req/s serial")
    print(f"  status: {dict(sorted(codes.items()))}")


if __name__ == "__main__":
    asyncio.run(main())
