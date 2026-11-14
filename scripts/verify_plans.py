"""Assert the query plans this repo depends on, against a live database.

    .venv/bin/python -I scripts/verify_plans.py

WHY THIS EXISTS. The /search/notices ranking query spent 1,778-2,040 ms projecting a view over
all 94,183 rows to decorate 20, and the code already said it must not: a comment block in
`api/main/domains/search/service.py` titled "RANK FIRST, THEN DECORATE" explained the failure,
named the cost, and was wrong about what the shipped SQL achieved. The intent was correct and
lived in a comment, so nothing noticed when the planner stopped honouring it.

A comment is advice. This is a check. It is the same reason .claude/hooks/guard.py exists for
the rules in CLAUDE.md.

Each assertion is about the PLAN, not the clock: a timing threshold on a laptop with four
containers running is a flake generator, while "does this plan read the whole table" is a
property. Buffer counts are included because they are the same kind of fact -- 661,886 buffer
hits to return 20 rows was the symptom, and ~1,200 is the shape after 0027.
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from api.main import db  # noqa: E402
from api.main.domains.search import encoder, service  # noqa: E402

CAPTURED: dict = {}


async def _capture(conn, sql, args, spatial):
    CAPTURED.setdefault("calls", []).append((sql, args, spatial))
    return []


async def notices_plan(conn, spatial: bool) -> str:
    """The plan for the SQL the real code path builds, not a hand-written approximation."""
    CAPTURED.clear()
    real, service._fetch = service._fetch, _capture
    try:
        kw = {"lng": 4.9003, "lat": 52.3791, "minutes": 10} if spatial else {}
        await service.search(q="附近有什么会吵的工程", date_from=date(2026, 1, 1),
                             date_to=date(2026, 12, 31), limit=20, **kw)
    finally:
        service._fetch = real
    sql, args, _ = CAPTURED["calls"][0]
    rows = await conn.fetch("EXPLAIN (ANALYZE, BUFFERS) " + sql, *args)
    return "\n".join(r[0] for r in rows)


def buffers(plan: str) -> int:
    """Total shared buffers touched by the top node."""
    m = re.search(r"Buffers: shared hit=(\d+)(?: read=(\d+))?", plan)
    return int(m.group(1)) + int(m.group(2) or 0) if m else -1


# Shared by both branches. `bekendmaking_body` holds the 72 MB TOAST relation; reading it whole
# is the 2 s that 0027 removed.
COMMON = [
    ("never seq-scans bekendmaking_body",
     lambda p: "Seq Scan on bekendmaking_body" not in p),
    ("...nor bekendmaking",
     lambda p: not re.search(r"Seq Scan on bekendmaking\b", p)),
    # 661,886 before 0027. A generous ceiling: this is a cliff detector, not a budget.
    ("buffers stay under 50,000 (was 661,886 before 0027)",
     lambda p: 0 <= buffers(p) < 50_000),
]

# The corpus-wide branch has no filter to narrow with, so 94,183 vectors must be reached
# through the index. If this stops holding the endpoint is a brute-force scan that merely
# happens to be fast enough to miss.
CORPUS = COMMON + [
    ("the HNSW index answers the ordering",
     lambda p: "bekendmaking_embedding_hnsw" in p),
]

# The isochrone branch deliberately does NOT use HNSW, and asserting that it does was this
# script's own first false positive. The spatial filter narrows to ~1,500 candidates, and an
# exact top-N heapsort over those beats an approximate index scan with iterative_scan -- same
# answers, 113 ms. What matters here is that the narrowing happens FIRST: the vector work must
# be bounded by the spatial hit set, not by the corpus.
ISOCHRONE = COMMON + [
    ("the spatial hit set is materialised before the vector work",
     lambda p: "CTE hit" in p and "CTE Scan on hit" in p),
    ("the vector sort is over the hit set, not the corpus",
     lambda p: bool(re.search(r"Sort Method: top-N heapsort", p))),
]

async def main() -> int:
    await db.connect()
    if not encoder.warm():
        print("SKIP: the query encoder is not installed (requirements-local.txt); "
              "this check needs a real query vector")
        await db.close()
        return 0
    bad = 0
    try:
        async with db.acquire() as conn:
            for label, spatial, checks in (("corpus-wide", False, CORPUS),
                                           ("isochrone", True, ISOCHRONE)):
                plan = await notices_plan(conn, spatial)
                print(f"=== /search/notices, {label} === ({buffers(plan):,} buffers)")
                for name, ok in checks:
                    passed = ok(plan)
                    bad += not passed
                    print(f"  {'ok  ' if passed else 'FAIL'} {name}")
                if any(not ok(plan) for _, ok in checks):
                    print("\n--- the plan ---\n" + re.sub(r"'\[[-0-9.,e ]+\]'::vector",
                                                          "'[<vector>]'::vector", plan))
    finally:
        await db.close()
    print(f"\n{'ALL PASS' if bad == 0 else str(bad) + ' FAILURES'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
