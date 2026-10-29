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


# Why plan_cache_mode=force_custom_plan is set on every pooled connection.
#
# asyncpg sends every query as a server-side prepared statement and caches it
# (statement_cache_size=100 by default). PostgreSQL re-plans a prepared statement with the
# actual parameter values for its first five executions, then decides whether a single
# generic plan is good enough and reuses it from the sixth onwards. Our spatial queries
# pass the search shape as a parameter — `ST_GeomFromGeoJSON($n)` for an isochrone — so at
# plan time the planner cannot see the polygon and has no bounding box to estimate
# selectivity from. The generic plan it settles on is built for the average case and is
# catastrophic for ours.
#
# MEASURED 2026-10-29 against bekendmaking_locatie (88,877 rows), one connection, the same
# 30-minute driving isochrone ten times (1,241 vertices, 54,414 matches):
#   asyncpg defaults            2.7  2.5  2.4  9.0  3.1  104.2s  <- generic plan, 6th call
#   plan_cache_mode=force_custom_plan   2.5-3.0s, all ten, no cliff
# Reproduced independently in psql with PREPARE/EXECUTE: executions 1-5 took 2.3-2.6 s and
# the 6th took 77.5 s. Same 30x step, so it is the plan cache, not asyncpg.
#
# Re-planning costs ~1 ms here against queries that take tens of ms, so the trade is
# one-sided. It is set pool-wide rather than per-query because `/pois` passes its polygon
# the same way and has the same exposure — it had simply never been run six times in one
# process against a shape this wide.
#
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
            DATABASE_URL, min_size=1, max_size=10, init=_init_connection,
            server_settings={"plan_cache_mode": "force_custom_plan"},
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
