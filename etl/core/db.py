"""One way to find the database, for everything under etl/.

Before 2026-11-14 there were three, and they were **not equivalent**:

    load_dotenv(ROOT / ".env"); os.getenv("DATABASE_URL")   9 files -- process env wins
    dotenv_values(ROOT / ".env").get("DATABASE_URL")        5 files -- process env IGNORED
    os.environ["DATABASE_URL"]                              4 experiments -- no .env at all

The middle one is the trap: exporting DATABASE_URL and running one of those scripts silently
used `.env` instead, so two scripts in the same shell could talk to two different databases.
That is now impossible for anything that imports this module.

THE ONE DELIBERATE BEHAVIOUR CHANGE of the etl/core extraction: the five `dotenv_values`
callers now let an exported DATABASE_URL win, as the other nine always did. Visible only when
both are set and they differ -- in which case the old behaviour was the surprising one.

Importing this module loads `.env`, which is why ETL modules import it FIRST, before any
`from api.main...` import: `api/main/config.py` calls a bare `load_dotenv()` that resolves
relative to the working directory, and this one is anchored to the repo root.

The frozen scripts under etl/experiments/ are not converted. They keep whatever they had.
"""
from __future__ import annotations

import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

load_dotenv(ROOT / ".env")


def dsn(required: bool = True) -> str | None:
    """The DSN. `required=False` returns None instead of exiting, for the modules that carry
    DATABASE_URL as a module-level constant and check it themselves later."""
    value = os.getenv("DATABASE_URL")
    if not value and required:
        raise SystemExit("DATABASE_URL is not set")
    return value


async def connect() -> asyncpg.Connection:
    """One connection, for a CLI that does its work and exits. Callers close it.

    Not a pool: these are batch scripts, and `api/main/db.py` owns the pool that the request
    path needs. Deliberately no `plan_cache_mode` setting either -- that one is load-bearing
    for the API's parameterised spatial queries (2.4-3.1 s five times, 104 s on the sixth) and
    an ETL script issues a handful of statement shapes against a connection it then drops.
    """
    return await asyncpg.connect(dsn())
