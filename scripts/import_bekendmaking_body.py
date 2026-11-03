"""Fetch the publication DOCUMENT for rows already in `bekendmaking` and store its period.

    .venv/bin/python scripts/import_bekendmaking_body.py --family apv --limit 200
    .venv/bin/python scripts/import_bekendmaking_body.py --family all
    .venv/bin/python scripts/import_bekendmaking_body.py --retry

The SRU index record keeps a publication's period START date and drops the END date, so a
two-day skip container and 144 days of scaffolding are the same row in `bekendmaking`. That
made 51-54% of any geographic slice undeterminable for "is this noisy", at every radius tried
(docs/pg-notes/noise-groundtruth.md). This recovers the dropped fields.

Reads `<identifier>.xml`, NOT the .html page: 5,225 bytes against 27,668 on the same
document, and a marker-based HTML scraper failed to extract 2 of 30 bodies while both parse
cleanly from XML. Choosing the structured endpoint removed the failure mode rather than
handling it.

Nothing is inferred. Every period column stays NULL unless the document stated the value,
and `status` says which of the six cases applies, because a ground truth cannot tell a
fabricated end date from a real one.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import date, time
from pathlib import Path

import asyncpg
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DOC = "https://zoek.officielebekendmakingen.nl/{}.xml"
CONCURRENCY = 4          # a public government endpoint, not ours; see --concurrency
UA = "mapscale-research/0.1 (personal project; CC0 open data)"

_CLAIM_RUN = "INSERT INTO etl_run (source, note) VALUES ($1, $2) RETURNING id"

# `fetched_at` is deliberately absent from the IS DISTINCT FROM comparison. It changes on
# every run by definition, so including it would rewrite every tuple on a no-op re-run and
# double the heap -- the trap stage 1 hit on `poi` and `import_bekendmaking.py` documents.
# The cost is that fetched_at means "when the stored content was last seen to change", not
# "when we last looked"; that is the useful one of the two anyway.
_UPSERT = """
INSERT INTO bekendmaking_body (identifier, status, start_date, end_date, start_time,
                               end_time, period_raw, visitors_peak, body_text, etl_run)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
ON CONFLICT (identifier) DO UPDATE SET
    status = EXCLUDED.status, start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date,
    start_time = EXCLUDED.start_time, end_time = EXCLUDED.end_time,
    period_raw = EXCLUDED.period_raw, visitors_peak = EXCLUDED.visitors_peak,
    body_text = EXCLUDED.body_text, etl_run = EXCLUDED.etl_run, fetched_at = now()
WHERE (bekendmaking_body.status, bekendmaking_body.start_date, bekendmaking_body.end_date,
       bekendmaking_body.start_time, bekendmaking_body.end_time,
       bekendmaking_body.period_raw, bekendmaking_body.visitors_peak,
       bekendmaking_body.body_text)
   IS DISTINCT FROM
      (EXCLUDED.status, EXCLUDED.start_date, EXCLUDED.end_date, EXCLUDED.start_time,
       EXCLUDED.end_time, EXCLUDED.period_raw, EXCLUDED.visitors_peak, EXCLUDED.body_text)
RETURNING (xmax = 0) AS inserted
"""

# The two families whose fields the index drops. `activiteit`/`doc_type` alone do not identify
# them -- the APV family is recognisable only from the abstract's own prefix -- so this matches
# the same way the tier rules in noise-groundtruth.md do, text first and doc_type as well.
_SCOPE = {
    "apv": "(coalesce(b.title,'')||' '||coalesce(b.abstract,'')) ~* "
           "'objectvergunning|\\yObject\\y|tijdelijke verkeersmaatregel|\\yTVM\\y|nachtwerk'",
    "evenement": "b.doc_type ~* 'evenement' OR "
                 "(coalesce(b.title,'')||' '||coalesce(b.abstract,'')) ~* 'evenement'",
}
_SCOPE["all"] = f"({_SCOPE['apv']}) OR ({_SCOPE['evenement']})"

_AL = re.compile(r"<al\b[^>]*>(.*?)</al>", re.S)
_TAG = re.compile(r"<[^>]+>")
_D = r"(\d{1,2}-\d{1,2}-\d{4})"
_T = r"(\d{1,2}[:.]\d{2})"

# 'Looptijd :22-05-2026 t/m 04-06-2026' -- note the space before the colon and none after.
_LOOPTIJD = re.compile(rf"Looptijd\s*:?\s*{_D}(?:\s*(?:t/m|tot en met|tot)\s*{_D})?", re.I)
_DATUM_VAN = re.compile(rf"Datum\s+van\s*:?\s*{_D}", re.I)
_DATUM_TM = re.compile(rf"Datum\s*(?:t/m|tot en met|tot)\s*:?\s*{_D}", re.I)
_TIJD_VAN = re.compile(rf"Tijd\s+van\s*:?\s*{_T}", re.I)
_TIJD_TOT = re.compile(rf"Tijd\s*(?:tot en met|t/m|tot)\s*:?\s*{_T}", re.I)
_BEZOEKERS = re.compile(r"Bezoekers\s+drukste\s+moment\s*:?\s*(\d{1,7})", re.I)


def _unesc(s: str) -> str:
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&#39;", "'"), ("&nbsp;", " ")):
        s = s.replace(a, b)
    return s


def _d(s: str) -> date | None:
    """dd-mm-yyyy, validated. '31-04-2026' really occurs upstream (1 row in 20,689 of an
    earlier probe, 0.005%) and date() is what catches it -- a format check would not."""
    dd, mm, yy = (int(x) for x in s.split("-"))
    try:
        return date(yy, mm, dd)
    except ValueError:
        return None


def _t(s: str) -> time | None:
    """'09:00' and '14.00' both occur in Amsterdam evenement publications. An hour of 24 is
    returned as None rather than clamped to 23:59 or rolled to the next day: both would be a
    value the publication does not state. period_raw keeps the original either way."""
    h, m = (int(x) for x in s.replace(".", ":").split(":"))
    try:
        return time(h, m)
    except ValueError:
        return None


def parse_body(xml: str) -> dict:
    """XML text -> a row dict. Pure, so tests need no network."""
    if "</metadata>" not in xml:
        return {"status": "no_body_element"}
    body = xml.split("</metadata>", 1)[1]
    if not body.strip():
        return {"status": "no_body_element"}

    lines = []
    for raw in _AL.findall(body):
        t = re.sub(r"\s+", " ", _unesc(_TAG.sub("", raw))).strip()
        if t:
            lines.append(t)
    if not lines:
        return {"status": "empty_body"}
    text = "\n".join(lines)

    # Every period key is present and None, never absent. A caller must be able to read
    # out["end_time"] and be told "the document does not say" instead of raising KeyError --
    # the table's whole discipline is that absence is stated, not implied. Found by
    # tests/unit/test_bekendmaking_body_parse.py; the importer's .get() had been hiding it.
    out: dict = {"body_text": text, "status": "no_period", "start_date": None,
                 "end_date": None, "start_time": None, "end_time": None,
                 "period_raw": None, "visitors_peak": None}
    raws: list[str] = []

    if m := _LOOPTIJD.search(text):
        out["start_date"] = _d(m.group(1))
        out["end_date"] = _d(m.group(2)) if m.group(2) else None
        raws.append(m.group(0))
    else:
        if m := _DATUM_VAN.search(text):
            out["start_date"] = _d(m.group(1))
            raws.append(m.group(0))
        if m := _DATUM_TM.search(text):
            out["end_date"] = _d(m.group(1))
            raws.append(m.group(0))
    if m := _TIJD_VAN.search(text):
        out["start_time"] = _t(m.group(1))
        raws.append(m.group(0))
    if m := _TIJD_TOT.search(text):
        out["end_time"] = _t(m.group(1))
        raws.append(m.group(0))
    if m := _BEZOEKERS.search(text):
        out["visitors_peak"] = int(m.group(1))

    if raws:
        out["period_raw"] = " | ".join(raws)
        # 'ok' means a period field was PRESENT, not that it parsed: a row whose only date was
        # 31-04-2026 is ok with start_date NULL and the bad text in period_raw. Calling that
        # no_period would hide a source defect behind a legitimate-looking state.
        out["status"] = "ok"
    return out


async def _one(client: httpx.AsyncClient, sem: asyncio.Semaphore, ident: str) -> dict:
    async with sem:
        try:
            r = await client.get(DOC.format(ident), headers={"User-Agent": UA})
            if r.status_code != 200:
                return {"status": "fetch_error"}
            xml = r.text
        except (httpx.HTTPError, httpx.InvalidURL):
            return {"status": "fetch_error"}
    try:
        return parse_body(xml)
    except Exception:
        return {"status": "parse_error"}


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--family", choices=sorted(_SCOPE), default="all")
    p.add_argument("--limit", type=int, default=0, help="stop after N publications (0 = all)")
    p.add_argument("--retry", action="store_true",
                   help="re-attempt rows stored as fetch_error/parse_error/empty_body")
    p.add_argument("--refresh", action="store_true",
                   help="re-fetch rows already stored (idempotency check)")
    p.add_argument("--concurrency", type=int, default=CONCURRENCY)
    args = p.parse_args()

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")

    if args.retry:
        where = ("b.identifier IN (SELECT identifier FROM bekendmaking_body "
                 "WHERE status IN ('fetch_error','parse_error','empty_body'))")
    elif args.refresh:
        where = f"({_SCOPE[args.family]})"
    else:
        where = (f"({_SCOPE[args.family]}) AND NOT EXISTS "
                 "(SELECT 1 FROM bekendmaking_body x WHERE x.identifier = b.identifier)")
    sql = f"SELECT b.identifier FROM bekendmaking b WHERE {where} ORDER BY b.identifier"
    if args.limit:
        sql += f" LIMIT {args.limit}"

    conn = await asyncpg.connect(dsn)
    try:
        idents = [r["identifier"] for r in await conn.fetch(sql)]
        print(f"{len(idents)} publication(s) to fetch (family={args.family}, "
              f"retry={args.retry}, refresh={args.refresh})")
        if not idents:
            return 0

        source = f"bekendmaking_body:{args.family}"
        try:
            run_id = await conn.fetchval(_CLAIM_RUN, source, f"{len(idents)} queued")
        except asyncpg.UniqueViolationError:
            raise SystemExit(f"an import of {source} is already running") from None
        print(f"etl_run {run_id} claimed")

        stats: dict[str, int] = {}
        ins = upd = 0
        try:
            sem = asyncio.Semaphore(args.concurrency)
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                for i in range(0, len(idents), 200):
                    chunk = idents[i:i + 200]
                    rows = await asyncio.gather(*(_one(client, sem, x) for x in chunk))
                    for ident, row in zip(chunk, rows):
                        stats[row["status"]] = stats.get(row["status"], 0) + 1
                        r = await conn.fetchval(
                            _UPSERT, ident, row["status"], row.get("start_date"),
                            row.get("end_date"), row.get("start_time"), row.get("end_time"),
                            row.get("period_raw"), row.get("visitors_peak"),
                            row.get("body_text"), run_id,
                        )
                        if r is True:
                            ins += 1
                        elif r is False:
                            upd += 1
                    print(f"  {min(i + 200, len(idents))}/{len(idents)}  {stats}", flush=True)
            await conn.execute(
                "UPDATE etl_run SET status='succeeded', finished_at=now(), row_count=$2, "
                "note=$3 WHERE id=$1", run_id, len(idents), repr(stats))
        except Exception as e:
            await conn.execute(
                "UPDATE etl_run SET status='failed', finished_at=now(), note=$2 WHERE id=$1",
                run_id, f"{type(e).__name__}: {e}")
            raise

        print(f"\nINSERT {ins}, UPDATE {upd}, unchanged {len(idents) - ins - upd}")
        print(f"statuses: {stats}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
