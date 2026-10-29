import logging
import sys
import time
import uuid
from contextvars import ContextVar

from pythonjsonlogger.json import JsonFormatter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from . import metrics

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def _build_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
    ))
    handler.addFilter(_RequestIdFilter())
    return handler


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(_build_handler())
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def _route_label(request: Request) -> str:
    """The route TEMPLATE, never the raw path.

    `/api/main/pois?lng=4.9&lat=52.37` as a label value would make one time series per
    coordinate the browser ever sends — unbounded cardinality, which is the standard way
    to kill a Prometheus server. The template collapses them to one series per endpoint.
    Starlette fills `scope["route"]` during routing, so this is only valid AFTER
    call_next; a request that matched nothing has no route and is bucketed as "unmatched"
    rather than being labelled with whatever the caller sent.
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    _log = get_logger("middleware.request")

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = uuid.uuid4().hex[:8]
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        metrics.inc_in_flight(1)

        self._log.info("request_start", extra={
            "method": request.method,
            "path": request.url.path,
            "query": str(request.query_params),
        })

        try:
            response = await call_next(request)
            elapsed = time.perf_counter() - start
            metrics.observe_request(
                request.method, _route_label(request), response.status_code, elapsed
            )
            duration_ms = round(elapsed * 1000)
            level = "warning" if response.status_code >= 400 else "info"
            getattr(self._log, level)("request_end", extra={
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            })
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as e:
            elapsed = time.perf_counter() - start
            metrics.observe_request(request.method, _route_label(request), 500, elapsed)
            self._log.error("request_unhandled_exception", extra={
                "duration_ms": round(elapsed * 1000), "error": str(e),
            }, exc_info=True)
            raise
        finally:
            metrics.inc_in_flight(-1)
            request_id_var.reset(token)
