"""Import official Dutch announcements (officiële bekendmakingen) from the KOOP SRU service.

    .venv/bin/python scripts/import_bekendmaking.py --gemeente Amsterdam --from 2026-01-01
    .venv/bin/python scripts/import_bekendmaking.py --rd "126082 483821 5" --from 2025-01-01

Scope is either a municipality name or an RD New circle "X Y radius_km", because those are
the only two ways the service will narrow by place: `w.locatiepunt within/rijksdriehoek`
takes a circle and there is no polygon relation and no /wgs84 modifier (measured — an
unmodified `within` answers "wrong number of arguments" without saying a modifier is what is
missing). Clipping to a real isochrone therefore happens in PostGIS, not upstream.

No sentinel check here, unlike import_poi.py. That one soft-deletes rows the extract no
longer contains, so a truncated download would record a mass disappearance; this importer
only ever upserts, so a short fetch costs coverage, never correctness.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import date, timedelta

import asyncpg
import httpx

SRU = "https://repository.overheid.nl/sru"
PAGE = 100  # the service's own maximum per request

# Deep paging is refused, and not politely: startRecord 9901 answers 200 with an empty page
# while 10000 and beyond answer HTTP 504 Gateway Time-out. Measured, after an Amsterdam import
# died at exactly that offset with 53,719 records still to go. So a result set is only
# reachable to its first 10,000 records and anything larger has to be cut into windows that
# each stay below the ceiling — by date, which is the only axis that partitions cleanly.
PAGE_CEILING = 9900

_CLAIM_RUN = "INSERT INTO etl_run (source, note) VALUES ($1, $2) RETURNING id"

# ON CONFLICT DO UPDATE ... WHERE the row actually differs. Without the WHERE, a re-run of an
# overlapping date range rewrites every tuple and doubles the heap for no change — the same
# trap stage 1 hit on poi (docs/pg-notes/stage1-poi-import.md).
_UPSERT = """
INSERT INTO bekendmaking (identifier, title, doc_type, published_at, authority,
                          organisatietype, publicatienaam, gemeente, activiteit, abstract,
                          url, raw, first_seen_run, last_changed_run)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$13)
ON CONFLICT (identifier) DO UPDATE SET
    title = EXCLUDED.title, doc_type = EXCLUDED.doc_type,
    published_at = EXCLUDED.published_at, authority = EXCLUDED.authority,
    organisatietype = EXCLUDED.organisatietype, publicatienaam = EXCLUDED.publicatienaam,
    gemeente = EXCLUDED.gemeente, activiteit = EXCLUDED.activiteit,
    abstract = EXCLUDED.abstract, url = EXCLUDED.url, raw = EXCLUDED.raw,
    last_changed_run = EXCLUDED.last_changed_run
WHERE (bekendmaking.title, bekendmaking.doc_type, bekendmaking.published_at,
       bekendmaking.authority, bekendmaking.organisatietype, bekendmaking.publicatienaam,
       bekendmaking.gemeente, bekendmaking.activiteit, bekendmaking.abstract,
       bekendmaking.url, bekendmaking.raw)
   IS DISTINCT FROM
      (EXCLUDED.title, EXCLUDED.doc_type, EXCLUDED.published_at, EXCLUDED.authority,
       EXCLUDED.organisatietype, EXCLUDED.publicatienaam, EXCLUDED.gemeente,
       EXCLUDED.activiteit, EXCLUDED.abstract, EXCLUDED.url, EXCLUDED.raw)
"""

_INSERT_LOC = """
INSERT INTO bekendmaking_locatie (identifier, geom, label, postcode, straatnaam,
                                  huisnummer, woonplaats, published_at)
-- ST_MakeValid at write time, not read time: a self-intersecting ring makes
-- ST_Contains return an undefined answer instead of raising, and these rows are
-- stored. CollectionExtract(...,3) because MakeValid on a bad ring can yield a
-- GeometryCollection. Guarded by ST_IsValid so lines and points pass through
-- untouched — only polygonal input can be invalid.
SELECT $1, CASE WHEN ST_IsValid(g) THEN g ELSE ST_CollectionExtract(ST_MakeValid(g), 3) END,
       $3, $4, $5, $6, $7, $8
FROM (SELECT ST_SetSRID(ST_GeomFromText($2), 4326) AS g) s
ON CONFLICT (identifier, md5(ST_AsBinary(geom))) DO NOTHING
"""

# Fields promoted to columns; everything else in the record goes to raw.
_WKT = re.compile(r"\s*(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON)\s*\(")
_LATLON = re.compile(r"^\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*$")

_PROMOTED = {
    "identifier", "title", "type", "date", "creator", "organisatietype", "publicatienaam",
    "ligtInGemeente", "activiteit", "abstract", "preferredUrl", "locatiepunt", "geometrie",
    "locatiegebied", "geometrielabel", "postcode", "straatnaam", "huisnummer", "woonplaats",
    "itemUrl", "url",
}


def _all(rec: str, tag: str) -> list[str]:
    return [m.strip() for m in re.findall(r"<(?:\w+:)?%s[^>]*>([^<]*)</" % tag, rec) if m.strip()]


def _one(rec: str, tag: str) -> str | None:
    v = _all(rec, tag)
    return v[0] if v else None


def _parse(rec: str) -> dict | None:
    ident = _one(rec, "identifier")
    title = _one(rec, "title")
    dtype = _one(rec, "type")
    when = _one(rec, "date")
    if not (ident and title and dtype and when):
        return None

    # Every tag not promoted to a column, so nothing in the record is silently discarded.
    raw: dict[str, list[str]] = {}
    for tag, val in re.findall(r"<(?:\w+:)?([a-zA-Z][\w.-]*)[^>]*>([^<]+)</", rec):
        if tag not in _PROMOTED and val.strip():
            raw.setdefault(tag, []).append(val.strip())

    # `locatiegebied`, NOT `locatiepunt`. locatiepunt is only the point subset: over 100
    # Diemen records it was present on 46 while locatiegebied was present on 100, and the
    # missing 54 are the LINESTRING and POLYGON shapes. Reading locatiepunt dropped them
    # silently — see migrations/0006_bekendmaking_geometry.sql.
    #
    # Three formats in one field. The WKT ones are lon lat; the bare pair is lat,lon, i.e.
    # the AXIS ORDER IS REVERSED between them. Normalised to WKT here so the SQL has one path.
    shapes = []
    for g in _all(rec, "locatiegebied"):
        if _WKT.match(g):
            shapes.append(g)
            continue
        m = _LATLON.match(g)
        if m:
            shapes.append(f"POINT({m.group(2)} {m.group(1)})")

    return {
        "identifier": ident,
        "title": title,
        "doc_type": dtype,
        "published_at": date.fromisoformat(when[:10]),
        "authority": _one(rec, "creator"),
        "organisatietype": _one(rec, "organisatietype"),
        "publicatienaam": _one(rec, "publicatienaam"),
        "gemeente": _one(rec, "ligtInGemeente"),
        "activiteit": _all(rec, "activiteit"),
        "abstract": _one(rec, "abstract"),
        "url": _one(rec, "preferredUrl") or _one(rec, "url") or "",
        "raw": json.dumps(raw),
        "shapes": shapes,
        "label": _one(rec, "geometrielabel"),
        "postcode": _one(rec, "postcode"),
        "straatnaam": _one(rec, "straatnaam"),
        "huisnummer": _one(rec, "huisnummer"),
        "woonplaats": _one(rec, "woonplaats"),
    }


async def _page(client: httpx.AsyncClient, cql: str, start: int,
                probe_only: bool = False) -> tuple[list[str], int]:
    r = await client.get(SRU, params={
        "operation": "searchRetrieve", "version": "2.0", "query": cql,
        "maximumRecords": 0 if probe_only else PAGE, "startRecord": start,
    })
    r.raise_for_status()
    x = r.text
    total = re.search(r"<(?:\w+:)?numberOfRecords>(\d+)</", x)
    if total is None:
        diag = re.search(r"<(?:\w+:)?details>([^<]+)<", x)
        raise SystemExit(f"SRU rejected the query: {diag.group(1) if diag else x[:200]}")
    return re.findall(r"<(?:\w+:)?recordData>(.*?)</(?:\w+:)?recordData>", x, re.S), int(total.group(1))


def _window_cql(scope: str, lo: date, hi: date) -> str:
    return f'{scope} and dt.date>="{lo.isoformat()}" and dt.date<"{hi.isoformat()}"'


def _month_starts(lo: date, hi: date):
    cur = date(lo.year, lo.month, 1)
    while cur < hi:
        nxt = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
        yield max(cur, lo), min(nxt, hi)
        cur = nxt


def _day_starts(lo: date, hi: date):
    cur = lo
    while cur < hi:
        yield cur, cur + timedelta(days=1)
        cur += timedelta(days=1)


async def _plan(client: httpx.AsyncClient, scope: str, lo: date, hi: date) -> list[tuple[str, int]]:
    """Cut [lo, hi) into windows that each fit under PAGE_CEILING.

    Whole range first, because one window is one `numberOfRecords` probe and most scopes fit.
    Months next, then days. A single day over the ceiling is left as-is and will import its
    first 9,900 records: no municipality publishes that much in a day, and silently dropping
    the window would be worse than importing most of it and saying so.
    """
    whole = _window_cql(scope, lo, hi)
    _, total = await _page(client, whole, 1, probe_only=True)
    if total <= PAGE_CEILING:
        return [(whole, total)]

    print(f"  {total} records exceeds the {PAGE_CEILING} paging ceiling — slicing by month")
    out: list[tuple[str, int]] = []
    for mlo, mhi in _month_starts(lo, hi):
        cql = _window_cql(scope, mlo, mhi)
        _, n = await _page(client, cql, 1, probe_only=True)
        if n == 0:
            continue
        if n <= PAGE_CEILING:
            out.append((cql, n))
            continue
        print(f"    {mlo:%Y-%m} has {n} — slicing by day")
        for dlo, dhi in _day_starts(mlo, mhi):
            dcql = _window_cql(scope, dlo, dhi)
            _, dn = await _page(client, dcql, 1, probe_only=True)
            if dn:
                if dn > PAGE_CEILING:
                    print(f"      {dlo} has {dn}, above the ceiling — importing the first {PAGE_CEILING}")
                out.append((dcql, dn))
    return out


def _scope_cql(args) -> str:
    if args.gemeente:
        return f'w.ligtInGemeente=="{args.gemeente}"'
    return f'w.locatiepunt within/rijksdriehoek "{args.rd}"'


async def _import_window(client: httpx.AsyncClient, conn, run_id: int, cql: str,
                         total: int, done: int, grand: int) -> tuple[int, int, int]:
    """Page one date window into the two tables.

    Returns (publications, location rows, unparseable records). The last one is returned
    rather than swallowed: a record missing identifier/title/type/date is dropped here, and a
    silent drop is indistinguishable from the upstream simply having fewer records.
    """
    seen = locs = skipped = 0
    start = 1
    while start <= min(total, PAGE_CEILING):
        recs, _ = await _page(client, cql, start)
        if not recs:
            break
        parsed = [_parse(x) for x in recs]
        skipped += sum(1 for r in parsed if r is None)
        rows = [r for r in parsed if r]
        # One transaction per page, not per window: a window can be thousands of records, and
        # a single long transaction would hold its snapshot open against autovacuum for the
        # whole import.
        async with conn.transaction():
            for r in rows:
                await conn.execute(
                    _UPSERT, r["identifier"], r["title"], r["doc_type"], r["published_at"],
                    r["authority"], r["organisatietype"], r["publicatienaam"], r["gemeente"],
                    r["activiteit"], r["abstract"], r["url"], r["raw"], run_id,
                )
                for wkt in r["shapes"]:
                    await conn.execute(
                        _INSERT_LOC, r["identifier"], wkt, r["label"], r["postcode"],
                        r["straatnaam"], r["huisnummer"], r["woonplaats"],
                        r["published_at"],
                    )
                    locs += 1
        seen += len(rows)
        print(f"  {done + seen}/{grand}", flush=True)
        start += PAGE
    return seen, locs, skipped


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--gemeente", help='municipality name, e.g. "Amsterdam"')
    g.add_argument("--rd", help='RD New circle: "X Y radius_km"')
    p.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD, inclusive")
    p.add_argument("--to", dest="date_to", help="YYYY-MM-DD, exclusive")
    p.add_argument("--max", type=int, default=0, help="stop after N records (0 = all)")
    args = p.parse_args()

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")

    cql = _scope_cql(args) + f' and dt.date>="{args.date_from}"'
    source = f"bekendmaking:{args.gemeente or args.rd}"
    conn = await asyncpg.connect(dsn)
    try:
        try:
            run_id = await conn.fetchval(_CLAIM_RUN, source, cql)
        except asyncpg.UniqueViolationError:
            raise SystemExit(f"an import of {source} is already running") from None
        print(f"etl_run {run_id} claimed\n  {cql}")

        seen = locs = skipped = 0
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                hi = (date.fromisoformat(args.date_to) if args.date_to
                      else date.today() + timedelta(days=1))
                plan = await _plan(client, _scope_cql(args),
                                   date.fromisoformat(args.date_from), hi)
                grand = sum(n for _, n in plan)
                print(f"  {len(plan)} window(s), {grand} records upstream")
                for wcql, wtotal in plan:
                    n, m, bad = await _import_window(client, conn, run_id, wcql, wtotal, seen, grand)
                    seen += n
                    locs += m
                    skipped += bad
                    if args.max and seen >= args.max:
                        break
            await conn.execute(
                "UPDATE etl_run SET status='succeeded', finished_at=now(), row_count=$2 WHERE id=$1",
                run_id, seen,
            )
        except Exception as e:
            await conn.execute(
                "UPDATE etl_run SET status='failed', finished_at=now(), note=$2 WHERE id=$1",
                run_id, f"{type(e).__name__}: {e}",
            )
            raise

        live = await conn.fetchval("SELECT count(*) FROM bekendmaking")
        lv = await conn.fetchval("SELECT count(*) FROM bekendmaking_locatie")
        print(f"\n{seen} publications processed ({skipped} unparseable), {locs} location rows written")
        print(f"bekendmaking: {live} rows, bekendmaking_locatie: {lv} rows")
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
