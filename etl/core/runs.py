"""The `etl_run` lifecycle: claim, succeed, fail.

Written out three times before 2026-11-14 -- in the POI, bekendmaking and bekendmaking_body
importers -- identical in contract and differing only in SQL whitespace (`status='failed'`
against `status = 'failed'`). The contract is enforced by the schema, not by convention:

    etl_run_finished_iff_terminal   CHECK ((status='running') = (finished_at IS NULL))
    etl_run_one_active              UNIQUE (source) WHERE finished_at IS NULL

so `claim` can only fail one way, and it is the way that matters: a second concurrent import
of the same source. Hence SystemExit rather than a retry -- two importers writing the same
rows is not something to resolve automatically.

The partial index being predicated on `finished_at IS NULL` is also why every exit path has
to close the row. A crashed run leaves `running` forever and blocks the next import; that is
the reason `fail()` exists at all, and why `bekendmaking.py --plan-only` closes its claim as
succeeded with `row_count=0` instead of just returning.

What this module deliberately does NOT own: the transaction. `poi.py` writes its success row
inside `async with conn.transaction()` together with the merge and the soft delete, so the
count and the data commit or roll back as one; the other two do not need that. A helper that
opened its own transaction would have taken that choice away from the caller.
"""
from __future__ import annotations

import asyncpg

_CLAIM = "INSERT INTO etl_run (source, note) VALUES ($1, $2) RETURNING id"

_SUCCEED = ("UPDATE etl_run SET status='succeeded', finished_at=now(), row_count=$2 "
            "WHERE id=$1")
_SUCCEED_NOTE = ("UPDATE etl_run SET status='succeeded', finished_at=now(), row_count=$2, "
                 "note=$3 WHERE id=$1")
_FAIL = "UPDATE etl_run SET status='failed', finished_at=now(), note=$2 WHERE id=$1"

_LAST_GOOD = """
SELECT row_count FROM etl_run
WHERE source = $1 AND status = 'succeeded' AND row_count IS NOT NULL
ORDER BY started_at DESC
LIMIT 1
"""


async def claim(conn, source: str, note: str) -> int:
    """Open a run row for `source`, or exit if one is already open.

    Must be committed on its own (no enclosing transaction) for the partial unique index to
    exclude a second concurrent PROCESS rather than merely a second transaction in this one.
    """
    try:
        return await conn.fetchval(_CLAIM, source, note)
    except asyncpg.UniqueViolationError:
        raise SystemExit(f"an import of {source} is already running") from None


async def succeed(conn, run_id: int, row_count: int, note: str | None = None) -> None:
    if note is None:
        await conn.execute(_SUCCEED, run_id, row_count)
    else:
        await conn.execute(_SUCCEED_NOTE, run_id, row_count, note)


async def fail(conn, run_id: int, note: str) -> None:
    await conn.execute(_FAIL, run_id, note)


async def fail_from(conn, run_id: int, exc: BaseException) -> None:
    """`fail` with the note format all three importers used: `TypeName: message`."""
    await fail(conn, run_id, f"{type(exc).__name__}: {exc}")


async def last_good_row_count(conn, source: str) -> int | None:
    """The row_count of the most recent succeeded run for `source`, or None.

    Only `poi.py` reads it, for its sentinel: a run that stages less than half of the last
    good one refuses to write the diff, because a truncated extract would otherwise soft-delete
    most of the table. It lives here because it is a question about etl_run, not about POI.
    """
    return await conn.fetchval(_LAST_GOOD, source)
