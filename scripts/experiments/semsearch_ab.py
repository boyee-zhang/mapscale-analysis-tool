"""A/B: does corpus-vocabulary-aware LLM expansion beat literal translation?

Both arms use the SAME retrieval engine (Postgres dutch FTS, ts_rank_cd) and the SAME
number of LLM calls (one). The only axis varied is whether the LLM is shown the corpus
vocabulary and asked to expand into terms that actually occur in it.
"""
import asyncio, json, os, re, sys, random
import asyncpg, httpx

ROOT = "/Users/zby/Documents/personalProject/mapscale-analysis-tool"
sys.path.insert(0, ROOT)
for line in open(f"{ROOT}/.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

DSN = os.environ["DATABASE_URL"]
KEY = os.environ["DEEPSEEK_API_KEY"]

QUERIES = [
    "我家附近有人要砍树吗",
    "附近有什么会吵的工程",
    "有人要占用我家门口的停车位吗",
    "附近有新开的餐厅或酒吧在申请许可吗",
    "有人要把一套房子拆成多套出租吗",
    "附近最近要办什么活动吗",
    "有人申请加盖阳台或改造屋顶吗",
    "附近有拍电影或拍摄活动吗",
    "残疾人专用停车位有什么变动吗",
    "有人要装电动车充电桩吗",
    "有人要在路边放建筑垃圾箱吗",
    "有什么跟自行车有关的决定吗",
    "附近有要拆房子的吗",
    "有人申请在外墙挂广告牌吗",
    "附近有环保或土壤污染相关的审批吗",
]

# Measured from the corpus itself, not from documentation.
VOCAB = """doc_type (15 values, 53% fall in the catch-all 'andere vergunning'):
andere vergunning, omgevingsvergunning, verkeersbesluit of -mededeling,
exploitatievergunning, evenementenvergunning, omgevingsmelding, andere beschikking,
ander besluit van algemene strekking, andere voorlichtingsinformatie,
overige overheidsinformatie, andere melding, participatie,
vergunning voor activiteiten op of in oppervlaktewater,
ruimtelijk plan of omgevingsdocument, evenementmelding

activiteit (9 values, EMPTY on 69% of rows):
bouwen, ruimtelijke ordening, kappen, milieu, reclame, uitweg en inrit, natuur,
slopen, brandveilig gebruik

Most frequent abstract prefixes, with row counts. These are the words that actually
occur in the text. Note the jargon abbreviations:
TVM parkeervak 12301 (TVM = Tijdelijke Verkeersmaatregel), Object 7462,
onttrekkingsvergunning voor een tweede woning 1292,
aanleg gehandicaptenparkeerplaats op kenteken 370, Filmen 350,
wijzigen kenteken gehandicaptenparkeerplaats 282,
aanleg twee elektrische oplaadvakken 241, Splitsingsvergunning 185, RVV 178,
TVM stremmen 173, opheffen gehandicaptenparkeerplaats 167, Fietsen 157,
Vergunning voor woningvormen 155, gevelreclame 132, dakkapel 123+81,
kappen van een boom 114, Nachtwerk 111, realiseren van een dakopbouw 85"""

PROMPT_A = """Translate this resident's question into Dutch search keywords for a
full-text search. Output ONLY a JSON object: {"terms": ["...", "..."]}
Give 3-8 terms. Use the words a direct translation would use."""

PROMPT_B = """You are building a full-text search query against a corpus of Dutch
municipal publications. Below is the ACTUAL vocabulary of that corpus, measured from the
data. Expand the resident's question into the terms that really occur in this corpus --
including jargon and abbreviations -- not a literal translation.

%s

Output ONLY a JSON object: {"terms": ["...", "..."], "doc_type": [...], "activiteit": [...]}
Give 3-12 terms. doc_type and activiteit are optional hard filters; leave them as empty
lists unless the question clearly implies one.""" % VOCAB


async def ask(client, system, question):
    r = await client.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {KEY}"},
        json={"model": "deepseek-chat", "temperature": 0,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": question}]},
        timeout=90.0,
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


def tsquery(terms):
    """Terms -> one tsquery string. Multi-word terms become phrase queries."""
    parts = []
    for t in terms:
        t = re.sub(r"[^\w\s-]", " ", str(t).lower()).strip()
        words = [w for w in t.split() if w]
        if words:
            parts.append(" <-> ".join(words))
    return " | ".join(dict.fromkeys(parts)) if parts else None


SQL = """
SELECT b.identifier, b.title, b.abstract, b.doc_type, b.activiteit,
       ts_rank_cd(to_tsvector('dutch', coalesce(b.title,'') || ' ' || coalesce(b.abstract,'')),
                  q.tq) AS rank
FROM bekendmaking b, to_tsquery('dutch', $1) q(tq)
WHERE to_tsvector('dutch', coalesce(b.title,'') || ' ' || coalesce(b.abstract,'')) @@ q.tq
  AND ($2::text[]  IS NULL OR cardinality($2::text[]) = 0 OR b.doc_type = ANY($2::text[]))
  AND ($3::text[]  IS NULL OR cardinality($3::text[]) = 0 OR b.activiteit && $3::text[])
ORDER BY rank DESC, b.published_at DESC
LIMIT 10
"""


async def main():
    pool = await asyncpg.create_pool(DSN, min_size=2, max_size=4)
    out = []
    async with httpx.AsyncClient() as client:
        for q in QUERIES:
            a, b = await asyncio.gather(ask(client, PROMPT_A, q), ask(client, PROMPT_B, q))
            rec = {"query": q, "arms": {}}
            for name, spec in (("A_literal", a), ("B_expanded", b)):
                terms = spec.get("terms") or []
                tq = tsquery(terms)
                rows = []
                if tq:
                    async with pool.acquire() as con:
                        rows = await con.fetch(SQL, tq,
                                               spec.get("doc_type") or [],
                                               spec.get("activiteit") or [])
                rec["arms"][name] = {
                    "terms": terms, "tsquery": tq,
                    "doc_type": spec.get("doc_type") or [],
                    "activiteit": spec.get("activiteit") or [],
                    "hits": [{"id": r["identifier"], "title": r["title"],
                              "abstract": r["abstract"], "doc_type": r["doc_type"],
                              "rank": float(r["rank"])} for r in rows],
                }
            out.append(rec)
            print(f"{q}  A={len(rec['arms']['A_literal']['hits'])} "
                  f"B={len(rec['arms']['B_expanded']['hits'])}", flush=True)
    await pool.close()

    D = os.path.dirname(os.path.abspath(__file__))
    json.dump(out, open(f"{D}/raw.json", "w"), ensure_ascii=False, indent=1)

    # Pool both arms, strip provenance, shuffle -> blind judging sheet.
    rng = random.Random(42)
    sheet = []
    for rec in out:
        pool_items, seen = [], set()
        for arm in ("A_literal", "B_expanded"):
            for h in rec["arms"][arm]["hits"]:
                if h["id"] not in seen:
                    seen.add(h["id"])
                    pool_items.append(h)
        rng.shuffle(pool_items)
        sheet.append({"query": rec["query"],
                      "candidates": [{"n": i + 1, "id": h["id"], "title": h["title"],
                                      "abstract": h["abstract"]}
                                     for i, h in enumerate(pool_items)]})
    json.dump(sheet, open(f"{D}/judge_sheet.json", "w"), ensure_ascii=False, indent=1)
    print(f"\nwrote raw.json and judge_sheet.json to {D}")

asyncio.run(main())
