"""
Prometheus instrumentation.

Degrades like everything else here: if `prometheus_client` is missing, every function below
becomes a no-op and `/metrics` 503s rather than returning an empty 200 — an empty 200 charts
as a flat zero that looks like "no traffic". That is the same pattern as `db.enabled()` and
`cache.py`.

It is now in requirements.txt, not requirements-local.txt. The reason it was local-only was
bundle size in a Vercel function, and that deployment is gone; the deploy image was installing
it separately anyway, because deploy/prometheus.yml scrapes api:8000/metrics. The degradation
path stays because an environment can still be built without it.

What is measured, and why each one is here rather than "because Prometheus has it":

- `http_request_duration_seconds` — the histogram, with buckets chosen from this app's
  own measured range (a 10-minute walk is ~0.1-0.7 s, the widest shape ~2 s). Default
  buckets top out at 10 s and would put every interesting request in one bucket.
- `http_requests_in_flight` — concurrency actually reached. The ceiling question is
  "what happens at N concurrent", so N has to be observable, not assumed from the load
  generator's side.
- `db_pool_*` — the pool is `max_size=10`. That is the most likely first wall, and
  pool exhaustion is invisible from the outside: requests just queue, so latency climbs
  with no error and no log line.
- `event_loop_lag_seconds` — the one metric that distinguishes "slow upstream" from
  "this process is saturated". Everything here is async; if the loop is starved, lag
  rises while every individual await still looks fast.
- `upstream_request_duration_seconds` — ORS is ~0.6 s of a 0.7 s request. Without this
  the HTTP histogram cannot say whether we are slow or our dependency is.
"""
from __future__ import annotations

import asyncio
import time

# NOT `from .logger import get_logger` at module scope. logger.py imports this module for
# the request middleware, so a top-level import back into it is a cycle: logger.py is only
# partially initialised at that point and get_logger does not exist yet. Verified — it
# raises ImportError on `import api.main.app`. The one place that logs does the import
# inside the function instead.

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest,
    )
    _ENABLED = True
except ImportError:  # pragma: no cover - an install without prometheus_client, not tests
    _ENABLED = False
    CONTENT_TYPE_LATEST = "text/plain"


def enabled() -> bool:
    return _ENABLED


if _ENABLED:
    # Buckets are this app's measured latencies, not the library defaults. Powers of ten
    # would waste every bucket between 10 ms and 1 s, which is where all of it lives.
    _LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0)

    REQUESTS = Counter(
        "http_requests_total", "HTTP requests", ["method", "route", "status"]
    )
    LATENCY = Histogram(
        "http_request_duration_seconds", "HTTP request latency",
        ["method", "route"], buckets=_LATENCY_BUCKETS,
    )
    IN_FLIGHT = Gauge("http_requests_in_flight", "Requests currently being served")

    UPSTREAM = Histogram(
        "upstream_request_duration_seconds", "Time spent in an upstream call",
        ["service"], buckets=_LATENCY_BUCKETS,
    )
    UPSTREAM_ERRORS = Counter(
        "upstream_errors_total", "Upstream calls that raised", ["service"]
    )

    POOL_SIZE = Gauge("db_pool_size", "Connections the pool currently holds")
    POOL_IDLE = Gauge("db_pool_idle", "Connections idle in the pool")
    POOL_MAX = Gauge("db_pool_max", "Pool max_size")

    LOOP_LAG = Gauge("event_loop_lag_seconds", "Scheduling delay of the asyncio loop")


def observe_request(method: str, route: str, status: int, seconds: float) -> None:
    if not _ENABLED:
        return
    REQUESTS.labels(method, route, str(status)).inc()
    LATENCY.labels(method, route).observe(seconds)


def observe_upstream(service: str, seconds: float, failed: bool = False) -> None:
    if not _ENABLED:
        return
    UPSTREAM.labels(service).observe(seconds)
    if failed:
        UPSTREAM_ERRORS.labels(service).inc()


class track_upstream:
    """`async with track_upstream("ors"):` around a call in upstream/."""

    def __init__(self, service: str):
        self.service = service

    async def __aenter__(self):
        self._start = time.perf_counter()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        observe_upstream(self.service, time.perf_counter() - self._start, exc is not None)
        return False


def inc_in_flight(delta: int) -> None:
    if _ENABLED:
        IN_FLIGHT.inc(delta)


def render() -> bytes:
    return generate_latest() if _ENABLED else b""


# --- sampled gauges -----------------------------------------------------------------
#
# Pool stats and loop lag are sampled by a background task rather than read inside the
# scrape handler. Reading them on scrape would mean they are only ever observed while a
# scrape is in flight, which is the one moment the pool is guaranteed not to be busy.

_SAMPLE_INTERVAL = 1.0
_sampler: asyncio.Task | None = None


async def _sample_loop() -> None:
    from . import db

    while True:
        t0 = time.perf_counter()
        await asyncio.sleep(_SAMPLE_INTERVAL)
        # Everything beyond the requested sleep is time the loop could not give us.
        LOOP_LAG.set(max(0.0, (time.perf_counter() - t0) - _SAMPLE_INTERVAL))

        pool = getattr(db, "_pool", None)
        if pool is not None:
            try:
                POOL_SIZE.set(pool.get_size())
                POOL_IDLE.set(pool.get_idle_size())
                POOL_MAX.set(pool.get_max_size())
            except Exception:  # pool closing mid-sample
                pass


def start_sampler() -> None:
    global _sampler
    if not _ENABLED or _sampler is not None:
        return
    from .logger import get_logger

    _sampler = asyncio.create_task(_sample_loop())
    get_logger("metrics").info(
        "metrics sampler started", extra={"interval_s": _SAMPLE_INTERVAL}
    )


async def stop_sampler() -> None:
    global _sampler
    if _sampler is not None:
        _sampler.cancel()
        try:
            await _sampler
        except asyncio.CancelledError:
            pass
        _sampler = None
