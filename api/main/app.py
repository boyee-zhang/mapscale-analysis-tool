from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from . import db, http_client, metrics
from .errors import ExternalServiceError, InternalError
from .logger import RequestLoggingMiddleware
from .logger import get_logger
from .routers import (isochrone, pois, bekendmakingen, directions, region, traffic, search,
                      notice_search, health)

logger = get_logger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.connect()
    metrics.start_sampler()
    yield
    await metrics.stop_sampler()
    await http_client.aclose()
    await db.close()


app = FastAPI(lifespan=lifespan)

# ── Middleware (order matters: first added = outermost) ──────────────────────
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global exception handlers ────────────────────────────────────────────────

@app.exception_handler(ExternalServiceError)
async def external_service_handler(request: Request, exc: ExternalServiceError):
    """Upstream failure — not our bug. Log WARNING, return 502/504."""
    status = 504 if exc.upstream_status in (504, 408) else 502
    logger.warning("external_service_error", extra={
        "service": exc.service,
        "upstream_status": exc.upstream_status,
        "detail": exc.detail,
    })
    return JSONResponse(status_code=status, content={
        "error": "upstream_error",
        "service": exc.service,
        "detail": exc.detail,
    })


@app.exception_handler(InternalError)
async def internal_error_handler(request: Request, exc: InternalError):
    """Our bug. Log ERROR (already logged in router), return 500."""
    logger.error("internal_error", extra={"detail": str(exc)})
    return JSONResponse(status_code=500, content={
        "error": "internal_error",
        "detail": "An unexpected error occurred.",
    })

# ── Metrics ──────────────────────────────────────────────────────────────────
#
# Deliberately NOT under /api/main. That prefix is what vercel.json routes to the
# serverless function and what the Vite dev proxy forwards; /metrics is a local-only
# operational surface and should not look like part of the public API. It also means a
# scrape never passes through the CORS middleware's allow-origin "*".


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    if not metrics.enabled():
        # 503, not an empty 200: Prometheus marks the target down and says why, instead
        # of charting a flat zero that looks like "no traffic".
        return PlainTextResponse(
            "prometheus_client not installed (it lives in requirements-local.txt)",
            status_code=503,
        )
    return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE_LATEST)


# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(isochrone.router)
app.include_router(pois.router)
app.include_router(bekendmakingen.router)
app.include_router(directions.router)
app.include_router(region.router)
app.include_router(traffic.router)
app.include_router(search.router)
# Address search and publication search are different things on purpose: `search` resolves a
# place name to a coordinate, `notice_search` finds publications by meaning. Sharing a prefix
# would make one look like a mode of the other.
app.include_router(notice_search.router)

