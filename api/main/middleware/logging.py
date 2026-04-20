import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..observability.logger import get_logger, request_id_var

logger = get_logger("middleware.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = uuid.uuid4().hex[:8]
        token = request_id_var.set(request_id)
        start = time.perf_counter()

        logger.info("request_start", extra={
            "method": request.method,
            "path": request.url.path,
            "query": str(request.query_params),
        })

        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start) * 1000)
            level = "warning" if response.status_code >= 400 else "info"
            getattr(logger, level)("request_end", extra={
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            })
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as e:
            duration_ms = round((time.perf_counter() - start) * 1000)
            logger.error("request_unhandled_exception", extra={
                "duration_ms": duration_ms,
                "error": str(e),
            }, exc_info=True)
            raise
        finally:
            request_id_var.reset(token)
