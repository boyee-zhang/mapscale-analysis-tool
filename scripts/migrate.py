"""
Forward-only SQL migration runner.

Usage:
    python scripts/migrate.py            # apply all pending
    python scripts/migrate.py --status   # list applied / pending, apply nothing

Conventions:
  - Files live in migrations/ and are named NNNN_description.sql
  - A file named NNNN_description.noxact.sql runs OUTSIDE a transaction. This is
    required for statements Postgres refuses inside a transaction block, notably
    CREATE INDEX CONCURRENTLY.
  - Applied versions are recorded in schema_migrations. There is no "down" —
    roll forward with a new migration instead.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "migrations"

load_dotenv(ROOT / ".env")

_VERSION_RE = re.compile(r"^(\d+)_")

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def discover() -> list[tuple[str, Path]]:
    """Return [(version, path)] sorted by version, rejecting duplicates."""
    found: dict[str, Path] = {}
    for path in MIGRATIONS_DIR.glob("*.sql"):
        m = _VERSION_RE.match(path.name)
        if not m:
            raise SystemExit(f"migration file does not start with NNNN_: {path.name}")
        version = m.group(1)
        if version in found:
            raise SystemExit(f"duplicate migration version {version}: {found[version].name} / {path.name}")
        found[version] = path
    return sorted(found.items())


async def main() -> None:
    import os

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")

    migrations = discover()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(_BOOTSTRAP)
        applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}

        if "--status" in sys.argv:
            for version, path in migrations:
                mark = "applied" if version in applied else "pending"
                print(f"{version}  {mark:8}  {path.name}")
            return

        pending = [(v, p) for v, p in migrations if v not in applied]
        if not pending:
            print("nothing to apply")
            return

        for version, path in pending:
            sql = path.read_text()
            noxact = path.name.endswith(".noxact.sql")
            record = "INSERT INTO schema_migrations (version) VALUES ($1)"
            print(f"applying {path.name}{' (no transaction)' if noxact else ''}")
            if noxact:
                await conn.execute(sql)
                await conn.execute(record, version)
            else:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(record, version)
        print(f"applied {len(pending)} migration(s)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
