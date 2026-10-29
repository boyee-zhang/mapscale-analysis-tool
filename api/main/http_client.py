"""
One shared httpx.AsyncClient for every outbound call in the process.

Why this module exists, with the measurement that justified it (2026-10-29):

Every upstream helper used to open its own client per call —
`async with httpx.AsyncClient(timeout=...) as client:` — in ten places. That looks
harmless and is the shape most examples show. It is not harmless: constructing an
AsyncClient builds a fresh TLS context from the system trust store, measured at
**13.88 ms of CPU** on this machine. The whole failing `kv_get` was 14.53 ms, so 95% of
it was the constructor, not the dead DNS it was blamed on.

`/bekendmakingen` calls `kv_get` once per request. With the Upstash host NXDOMAIN since
2026-10-28, every request paid ~14 ms of CPU for a cache lookup that could never hit:

    per-request CPU   22.3 ms -> 7.4 ms      (-67%)
    throughput        40.9 -> 107-124 req/s  (~3x)
    p95               1495 -> 335-452 ms

No business logic changed. The 14.9 ms recovered matches the 14.5 ms measured cost of one
kv_get, which is how we know the attribution is right rather than merely plausible.

Reusing one client also restores connection keep-alive across requests, which the
per-call pattern threw away by closing the pool every time.

Per-call `timeout=` and `headers=` still work and are how the call sites keep their own
values — only the client's construction is shared.
"""
from __future__ import annotations

import httpx

# Generous ceiling; every call site passes its own timeout per request.
_DEFAULT_TIMEOUT = httpx.Timeout(15.0)

# Sized for this process's concurrency, not for a crowd: the asyncpg pool is 10 and the
# single-flight map collapses duplicate ORS calls, so a handful of upstream sockets is
# plenty. Too large here just holds sockets open against upstreams that do not want them.
_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20)

_client: httpx.AsyncClient | None = None


def get() -> httpx.AsyncClient:
    """The shared client. Created on first use so importing this module is free."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, limits=_LIMITS)
    return _client


async def aclose() -> None:
    """Called from the app lifespan. Safe to call when nothing ever created a client."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
