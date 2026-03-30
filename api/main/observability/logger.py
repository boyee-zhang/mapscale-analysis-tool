import logging
import sys
from contextvars import ContextVar
from pythonjsonlogger.json import JsonFormatter

# Shared context variable — set once per request in middleware, flows through all async calls
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
    """Return a structured JSON logger scoped to *name*.

    Call once at module level:
        logger = get_logger("router.isochrone")
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(_build_handler())
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
