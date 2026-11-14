"""Import official Dutch announcements (officiële bekendmakingen) from the KOOP SRU service.

    .venv/bin/python -m etl.ingest.bekendmaking --gemeente Amsterdam --from 2026-01-01
    .venv/bin/python -m etl.ingest.bekendmaking --rd "126082 483821 5" --from 2025-01-01

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
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, timedelta


import asyncpg
import httpx
from ..core import db, runs

SRU = "https://repository.overheid.nl/sru"
# MEASURED 2026-11-04, correcting a comment that said 100 was "the service's own maximum":
# it is not. 1000 is honoured and is the real cap — the payload goes 455 KB -> 4.7 MB, so it
# is served, not silently truncated. Asking for MORE than 1000 fails in a shape worth knowing:
# 2000, 5000 and 10000 all answer HTTP 200 with a 971-byte body that echoes
# `<maximumRecords>10</maximumRecords>` and carries neither `numberOfRecords` nor a record. A
# client that counts records reads that as "no results"; `_page()` survives it only because it
# raises when `numberOfRecords` is absent, which is luck rather than design.
#
# Server time scales with records rather than with requests (5.1 ms/record at 100, 3.1 ms at
# 1000), so this buys ~1.6x throughput — but it cuts the request COUNT tenfold, and that is
# what politeness toward a public service is counted in.
PAGE = 1000

# Deep paging is refused: `startRecord >= 10000` answers HTTP 504 Gateway Time-out. Found when
# an Amsterdam import died at exactly that offset with 53,719 records still to go, so a result
# set is only reachable to its first ~10,000 records and anything larger has to be cut into
# windows that each stay below the ceiling — by date, the only axis that partitions cleanly.
#
# RE-MEASURED 2026-11-04 and one half of the original note no longer holds: it recorded that
# `startRecord 9901` answers 200 with an EMPTY page. It now returns a full page. The limit is
# on the offset asked for, not on the record reached — `startRecord=9990&maximumRecords=50`
# serves records 9990 to 10039 without complaint. Only the 504 at 10000 still stands, and
# 9900 is kept as the ceiling because it is below it with room to spare.
PAGE_CEILING = 9900


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

# Excluded from raw at ANY multiplicity, unlike the rest of _PROMOTED. bekendmaking_locatie
# holds these in full and in WGS84, and `geometrie` is the SAME polygon again in RD New
# (EPSG:28992) -- checked point for point against locatiegebied on gmb-2026-341525. A second
# copy is a kilobyte a row buying no question we could not already answer.
_GEOMETRY = {"geometrie", "locatiegebied", "locatiepunt"}


def _tag(el: ET.Element) -> str:
    """Local name, namespace prefix stripped.

    MEASURED over 50 Amsterdam records: 55 distinct local names and NOT ONE of them occurs
    under two namespace URIs, so collapsing `dcterms:x` and `overheid:x` onto one key loses
    nothing on this corpus. Keying by the full `{uri}local` instead would put a 60-character
    URI in front of every raw key.
    """
    return el.tag.rpartition("}")[2]


def _all(rec: ET.Element, tag: str) -> list[str]:
    """Every occurrence of `tag`, in document order.

    `.text` and not `itertext()` on purpose: it keeps this identical to the regex it replaces
    for leaf elements, and returns nothing for a container -- `gebiedsmarkering` must not
    start answering with the polygon nested inside it.
    """
    return [t for el in rec.iter() if _tag(el) == tag
            for t in ((el.text or "").strip(),) if t]


def _one(rec: ET.Element, tag: str) -> str | None:
    v = _all(rec, tag)
    return v[0] if v else None


def _collect(rec: ET.Element) -> dict[str, list[str]]:
    """Everything in the record no column holds, keyed `tag` and `tag@attribute`.

    The regex this replaces claimed in its own comment that "nothing in the record is
    silently discarded", and could not keep that promise by construction: `[^>]*>` ate every
    attribute, and `([^<]+)` matches only leaf text, so container elements were invisible.
    MEASURED on gmb-2026-341525 -- 39 elements, of which it stored 14 and dropped all 14
    attribute values, among them `hasVersion@resourceIdentifier` (the canonical document URL)
    and six `itemUrl@manifestation` values, the only thing telling html/pdf/xml/odt apart.

    Two rules, because "store everything" is not free:
      - Attributes are ALWAYS kept, promoted tag or not, since no column holds an attribute.
      - A promoted tag's TEXT is kept only when the tag REPEATS, because `_one()` then took
        the first value and the column lost the rest. `itemUrl` is the live case: it is in
        _PROMOTED, no column reads it, and it occurs six times.
    _GEOMETRY stays out either way.
    """
    out: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for el in rec.iter():
        counts[_tag(el)] = counts.get(_tag(el), 0) + 1
    for el in rec.iter():
        t = _tag(el)
        txt = (el.text or "").strip()
        if txt and t not in _GEOMETRY and (t not in _PROMOTED or counts[t] > 1):
            out.setdefault(t, []).append(txt)
        for k, v in el.attrib.items():
            name = k.rpartition("}")[2]
            if v.strip() and name != "xmlns" and not k.startswith("xmlns"):
                out.setdefault(f"{t}@{name}", []).append(v.strip())
    return out


def _parse(rec: ET.Element) -> dict | None:
    ident = _one(rec, "identifier")
    title = _one(rec, "title")
    dtype = _one(rec, "type")
    when = _one(rec, "date")
    if not (ident and title and dtype and when):
        return None

    raw = _collect(rec)

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
                probe_only: bool = False) -> tuple[list[ET.Element], int]:
    """One SRU page, as parsed elements.

    The WHOLE envelope is parsed, and records are handed on as Elements rather than as the
    substrings between `<recordData>` tags. A record cut out of the response is NOT
    well-formed XML on its own -- its namespace prefixes are declared on the envelope root,
    so ElementTree rejects such a fragment outright with "unbound prefix". The regex this
    replaces had no objection to it, which is exactly the problem: it answered anyway, and
    plausibly. A parser's failure mode is an exception; a regex's is a wrong answer.
    """
    r = await client.get(SRU, params={
        "operation": "searchRetrieve", "version": "2.0", "query": cql,
        "maximumRecords": 0 if probe_only else PAGE, "startRecord": start,
    })
    r.raise_for_status()
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        raise SystemExit(f"SRU returned XML that does not parse: {e}; body starts {r.text[:200]}")
    total = root.find(".//{*}numberOfRecords")
    # Still a SystemExit and not an empty page, for the reason the maximumRecords comment
    # above records: over 1000 the service answers 200 with a body carrying no
    # numberOfRecords at all, and a client that reads that as "no results" stops silently.
    if total is None or not (total.text or "").strip().isdigit():
        diag = root.find(".//{*}details")
        detail = (diag.text or "").strip() if diag is not None else r.text[:200]
        raise SystemExit(f"SRU rejected the query: {detail}")
    return root.findall(".//{*}recordData"), int(total.text.strip())


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
    """The CQL that selects a scope. Place-based, or — since 2026-11-04 — national by type.

    The module docstring and CLAUDE.md both used to say there was "no national mode" because
    SRU narrows by place in only two ways. That was true of narrowing BY PLACE and false as a
    statement about the service: a query that names no place at all is answered for the whole
    country. MEASURED: `dt.type=="evenementenvergunning" and dt.date>="2026-01-01"` returns
    34,481 records spread over small municipalities (Het Hogeland, Alphen-Chaam, Son en
    Breugel) as well as the large ones, so it is not a big-city artefact.

    This matters because the false half was load-bearing: it made national coverage look like
    342 separate imports instead of one argument.
    """
    if args.gemeente:
        return f'w.ligtInGemeente=="{args.gemeente}"'
    if args.doc_type:
        return f'dt.type=="{args.doc_type}"'
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
    g.add_argument("--doc-type", dest="doc_type",
                   help='NATIONAL, scoped by publication type instead of by place, e.g. '
                        '"evenementenvergunning". No place filter at all — see _scope_cql.')
    p.add_argument("--plan-only", action="store_true",
                   help="print the window plan and upstream counts, import nothing")
    p.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD, inclusive")
    p.add_argument("--to", dest="date_to", help="YYYY-MM-DD, exclusive")
    p.add_argument("--max", type=int, default=0, help="stop after N records (0 = all)")
    args = p.parse_args()

    dsn = db.dsn()

    cql = _scope_cql(args) + f' and dt.date>="{args.date_from}"'
    source = (f"bekendmaking:type={args.doc_type}" if args.doc_type
              else f"bekendmaking:{args.gemeente or args.rd}")
    conn = await asyncpg.connect(dsn)
    try:
        run_id = await runs.claim(conn, source, cql)
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
                if args.plan_only:
                    for wcql, wtotal in plan:
                        print(f"    {wtotal:7}  {wcql}")
                    # Release the etl_run claim rather than leaving it open: the partial
                    # unique index is predicated on `finished_at IS NULL`, so an abandoned
                    # row would block the real import that follows.
                    await runs.succeed(conn, run_id, 0, note="plan-only")
                    return
                for wcql, wtotal in plan:
                    n, m, bad = await _import_window(client, conn, run_id, wcql, wtotal, seen, grand)
                    seen += n
                    locs += m
                    skipped += bad
                    if args.max and seen >= args.max:
                        break
            await runs.succeed(conn, run_id, seen)
        except Exception as e:
            await runs.fail_from(conn, run_id, e)
            raise

        live = await conn.fetchval("SELECT count(*) FROM bekendmaking")
        lv = await conn.fetchval("SELECT count(*) FROM bekendmaking_locatie")
        print(f"\n{seen} publications processed ({skipped} unparseable), {locs} location rows written")
        print(f"bekendmaking: {live} rows, bekendmaking_locatie: {lv} rows")
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
