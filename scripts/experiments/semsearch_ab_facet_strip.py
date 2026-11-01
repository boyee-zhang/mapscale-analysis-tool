"""One change only: strip structural/facet words out of the OR term list.
Same LLM output, same engine, same judgments. Isolates the ranking poison."""
import asyncio, json, os, re, sys
import asyncpg
ROOT = "/Users/zby/Documents/personalProject/mapscale-analysis-tool"
for line in open(f"{ROOT}/.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k, v)
D = os.path.dirname(os.path.abspath(__file__))

# These are doc_type / activiteit values and generic permit nouns. They are FACETS.
# A word here carries no content: it matches thousands of rows and flattens the rank.
STOP = {"omgevingsvergunning", "verkeersbesluit", "vergunning", "aanvraag", "besluit",
        "melding", "exploitatievergunning", "evenementenvergunning", "omgevingsmelding",
        "bouwen", "wonen", "werkzaamheden", "maatregelen", "plaatsen", "aanleg",
        "verkeersbesluit of -mededeling", "andere vergunning", "omgeving", "buurt",
        "in de buurt", "ruimtelijke ordening", "tvm", "rvv", "object"}

def clean(terms):
    out = []
    for t in terms:
        s = re.sub(r"[^\w\s-]", " ", str(t).lower()).strip()
        if not s or s in STOP:
            continue
        words = [w for w in s.split() if w]
        # a single generic word is noise; a phrase containing one is still specific
        if len(words) == 1 and words[0] in STOP:
            continue
        out.append(" <-> ".join(words))
    return " | ".join(dict.fromkeys(out)) or None

SQL = """
SELECT b.identifier
FROM bekendmaking b, to_tsquery('dutch', $1) q(tq)
WHERE to_tsvector('dutch', coalesce(b.title,'')||' '||coalesce(b.abstract,'')) @@ q.tq
  AND (cardinality($2::text[]) = 0 OR b.doc_type = ANY($2::text[]))
  AND (cardinality($3::text[]) = 0 OR b.activiteit && $3::text[])
ORDER BY ts_rank_cd(to_tsvector('dutch', coalesce(b.title,'')||' '||coalesce(b.abstract,'')), q.tq) DESC,
         length(coalesce(b.title,'')||coalesce(b.abstract,'')) ASC
LIMIT 10
"""

async def main():
    raw   = json.load(open(f"{D}/raw.json"))
    sheet = json.load(open(f"{D}/judge_sheet.json"))
    judg  = json.load(open(f"{D}/judgments.json"))
    rel = {}
    for s in sheet:
        byn = {c["n"]: c["id"] for c in s["candidates"]}
        rel[s["query"]] = {byn[n] for n in judg[s["query"]]}

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=2, max_size=4)
    print(f"{'query':34} {'B was':>6} {'B+fix':>6}")
    print("-" * 50)
    tot_old = tot_new = 0
    for r in raw:
        q = r["query"]; R = rel[q]; spec = r["arms"]["B_expanded"]
        old = sum(1 for h in spec["hits"] if h["id"] in R)
        tq = clean(spec["terms"])
        new_ids = []
        if tq:
            async with pool.acquire() as con:
                new_ids = [x["identifier"] for x in await con.fetch(
                    SQL, tq, spec.get("doc_type") or [], spec.get("activiteit") or [])]
        # Only judged candidates count; an unjudged new doc is NOT credited.
        new = sum(1 for i in new_ids if i in R)
        unjudged = sum(1 for i in new_ids if i not in R and i not in
                       {c["id"] for s in sheet if s["query"] == q for c in s["candidates"]})
        tot_old += old; tot_new += new
        flag = f"  (+{unjudged} unjudged)" if unjudged else ""
        print(f"{q:34} {old:>4}/10 {new:>4}/10{flag}")
    print("-" * 50)
    print(f"B_expanded      precision@10 = {tot_old}/150 = {tot_old/150*100:.1f}%")
    print(f"B + facet-strip precision@10 = {tot_new}/150 = {tot_new/150*100:.1f}%  (lower bound:")
    print(f"                unjudged new docs are counted as irrelevant)")
    await pool.close()

asyncio.run(main())
