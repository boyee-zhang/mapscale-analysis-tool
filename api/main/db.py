"""
Postgres connection layer (asyncpg, no ORM).

The pool is created in the FastAPI lifespan. If DATABASE_URL is unset the pool
stays None and `enabled()` returns False — callers must then fall back to their
pre-Postgres path, the same way cache.py degrades when Upstash is unconfigured.

PostGIS geometry columns are read/written as text or as separate x/y expressions
in the SQL itself (ST_X / ST_Y / ST_SetSRID(ST_MakePoint(...))) rather than via a
custom codec, so that every query can be pasted straight into psql for EXPLAIN.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import asyncpg

from .config import DATABASE_URL
from .logger import get_logger

logger = get_logger("db")

_pool: asyncpg.Pool | None = None


def enabled() -> bool:
    return _pool is not None


async def _init_connection(conn: asyncpg.Connection) -> None:
    # Without this asyncpg hands back jsonb as a raw string.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def connect() -> None:
    global _pool
    if not DATABASE_URL:
        logger.info("db disabled", extra={"reason": "DATABASE_URL unset"})
        return
    try:
        _pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=1, max_size=10, init=_init_connection
        )
    except Exception as e:
        # A dead Postgres must not stop the app from serving the degraded paths.
        logger.error("db pool creation failed", exc_info=True, extra={"error": str(e)})
        return
    logger.info("db connected")


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("db closed")


@asynccontextmanager
async def acquire():
    """Yield a connection. Raises if the pool is unavailable — guard with enabled()."""
    if _pool is None:
        raise RuntimeError("db not configured")
    async with _pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction():
    """Yield a connection inside a transaction."""
    async with acquire() as conn:
        async with conn.transaction():
            yield conn
