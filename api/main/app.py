from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .errors import ExternalServiceError, InternalError
from .middleware.logging import RequestLoggingMiddleware
from .observability.logger import get_logger
from .observability.tracing import setup_tracing
from .observability.metrics import setup_metrics
from .routers import isochrone, pois, directions, region

logger = get_logger("app")

app = FastAPI()

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

# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(isochrone.router)
app.include_router(pois.router)
app.include_router(directions.router)
app.include_router(region.router)

# ── Observability (after routes so /metrics is excluded from tracing noise) ──
setup_tracing(app)
setup_metrics(app)
