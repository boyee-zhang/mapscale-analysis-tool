"""Is it "did not find" or "found but ranked low"?

Uses the EXISTING judgments. For each arm, re-runs its own tsquery with NO limit and asks
where the known-relevant documents actually land. Same ORDER BY as the original run, so
the ranks are the ones that run would have produced.

What this CAN say: for a document we know is relevant, did the arm's query match it at all,
and at what rank. What it CANNOT say: whether relevant documents exist that NEITHER arm ever
surfaced -- the relevant set is pool-derived. That needs real ground truth.
"""
import asyncio, json, os, re
import asyncpg

ROOT = "/Users/zby/Documents/personalProject/mapscale-analysis-tool"
for line in open(f"{ROOT}/.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k, v)
D = os.path.dirname(os.path.abspath(__file__))

def tsquery(terms):
    parts = []
    for t in terms:
        t = re.sub(r"[^\w\s-]", " ", str(t).lower()).strip()
        w = [x for x in t.split() if x]
        if w:
            parts.append(" <-> ".join(w))
    return " | ".join(dict.fromkeys(parts)) or None

FTS = "to_tsvector('dutch', coalesce(b.title,'') || ' ' || coalesce(b.abstract,''))"
SQL = f"""
WITH q AS (SELECT to_tsquery('dutch', $1) tq),
m AS (
  SELECT b.identifier,
         row_number() OVER (ORDER BY ts_rank_cd({FTS}, q.tq) DESC, b.published_at DESC) rn,
         count(*) OVER () total
  FROM bekendmaking b, q
  WHERE {FTS} @@ q.tq
    AND (cardinality($2::text[]) = 0 OR b.doc_type = ANY($2::text[]))
    AND (cardinality($3::text[]) = 0 OR b.activiteit && $3::text[])
)
SELECT identifier, rn, total FROM m WHERE identifier = ANY($4::text[])
"""

async def main():
    raw   = json.load(open(f"{D}/raw.json"))
    sheet = json.load(open(f"{D}/judge_sheet.json"))
    judg  = json.load(open(f"{D}/judgments.json"))
    rel = {}
    for s in sheet:
        byn = {c["n"]: c["id"] for c in s["candidates"]}
        rel[s["query"]] = [byn[n] for n in judg[s["query"]]]

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=2, max_size=4)
    print(f"{'query':30} {'arm':11} {'rel':>4} {'匹配':>5} {'top10':>6} {'11-100':>7} {'>100':>5} {'匹配集':>8}")
    print("-" * 92)
    agg = {}
    rows_out = []
    for r in raw:
        q = r["query"]; R = rel[q]
        for arm in ("A_literal", "B_expanded"):
            spec = r["arms"][arm]
            tq = tsquery(spec["terms"])
            async with pool.acquire() as con:
                got = await con.fetch(SQL, tq, spec.get("doc_type") or [],
                                      spec.get("activiteit") or [], R)
            ranks = sorted(x["rn"] for x in got)
            total = got[0]["total"] if got else 0
            t10 = sum(1 for x in ranks if x <= 10)
            t100 = sum(1 for x in ranks if 10 < x <= 100)
            tbig = sum(1 for x in ranks if x > 100)
            a = agg.setdefault(arm, dict(rel=0, matched=0, t10=0, t100=0, tbig=0))
            a["rel"] += len(R); a["matched"] += len(ranks)
            a["t10"] += t10; a["t100"] += t100; a["tbig"] += tbig
            print(f"{q[:28]:30} {arm:11} {len(R):>4} {len(ranks):>5} {t10:>6} {t100:>7} {tbig:>5} {total:>8}")
            rows_out.append(dict(query=q, arm=arm, n_rel=len(R), matched=len(ranks),
                                 top10=t10, r11_100=t100, over100=tbig,
                                 match_set=total, ranks=ranks))
    print("-" * 92)
    for arm, a in agg.items():
        miss = a["rel"] - a["matched"]
        print(f"{arm:11}  已知相关 {a['rel']}  |  查询压根不匹配 {miss} ({miss/a['rel']*100:.0f}%)"
              f"  |  匹配到了: top10 {a['t10']}, 11-100 {a['t100']}, >100 {a['tbig']}")
    print()
    for arm, a in agg.items():
        if a["matched"]:
            print(f"{arm:11}  匹配到的相关文档里,只有 {a['t10']}/{a['matched']} "
                  f"= {a['t10']/a['matched']*100:.0f}% 进了 top-10")
    json.dump(rows_out, open(f"{D}/diag.json", "w"), ensure_ascii=False, indent=1)
    await pool.close()

asyncio.run(main())
