"""2x2: vocabulary (no/yes) x instruction (translate/expand), schema held at terms-only.

A  = no vocab,  translate   (reuse from raw.json)
B' = vocab,     expand      (reuse B's terms, DISCARD its doc_type/activiteit filters)
C  = vocab,     translate    (new LLM call)
D  = no vocab,  expand       (new LLM call)

Holding the output schema at terms-only removes the third difference that the original
A-vs-B comparison silently carried.
"""
import asyncio, json, os, re, random
import asyncpg, httpx

ROOT = "/Users/zby/Documents/personalProject/mapscale-analysis-tool"
for line in open(f"{ROOT}/.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k, v)
D_ = os.path.dirname(os.path.abspath(__file__))
KEY = os.environ["DEEPSEEK_API_KEY"]

raw = json.load(open(f"{D_}/raw.json"))
QUERIES = [r["query"] for r in raw]
VOCAB = open(f"{D_}/vocab.txt").read()

P_TRANSLATE = """Translate this resident's question into Dutch search keywords for a
full-text search. Use the words a direct translation would use.
Output ONLY a JSON object: {"terms": ["...", "..."]}  Give 3-8 terms."""

P_EXPAND = """You are building a full-text search query against a corpus of Dutch municipal
publications (building permits, traffic decisions, event permits, APV permits). Expand the
resident's question into the terms that really occur in such a corpus -- including the jargon
and abbreviations Dutch municipalities actually use -- not a literal translation.
Output ONLY a JSON object: {"terms": ["...", "..."]}  Give 3-12 terms."""

P_VOCAB_TRANSLATE = """Translate this resident's question into Dutch search keywords for a
full-text search against a corpus of Dutch municipal publications. Use the words a direct
translation would use.

Below is the ACTUAL vocabulary of that corpus, measured from the data, for reference.

""" + VOCAB + """

Output ONLY a JSON object: {"terms": ["...", "..."]}  Give 3-8 terms."""

PROMPTS = {"C_vocab_translate": P_VOCAB_TRANSLATE, "D_novocab_expand": P_EXPAND}

async def ask(client, system, question):
    r = await client.post("https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {KEY}"},
        json={"model": "deepseek-chat", "temperature": 0,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": question}]}, timeout=90.0)
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])

def tsquery(terms):
    parts = []
    for t in terms:
        t = re.sub(r"[^\w\s-]", " ", str(t).lower()).strip()
        w = [x for x in t.split() if x]
        if w: parts.append(" <-> ".join(w))
    return " | ".join(dict.fromkeys(parts)) or None

FTS = "to_tsvector('dutch', coalesce(b.title,'') || ' ' || coalesce(b.abstract,''))"
SQL = f"""SELECT b.identifier, b.title, b.abstract
FROM bekendmaking b, to_tsquery('dutch',$1) q(tq)
WHERE {FTS} @@ q.tq
ORDER BY ts_rank_cd({FTS}, q.tq) DESC, b.published_at DESC LIMIT 10"""

async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=2, max_size=4)
    out = {}
    async with httpx.AsyncClient() as client:
        for rec in raw:
            q = rec["query"]
            arms = {"A_novocab_translate": rec["arms"]["A_literal"]["terms"],
                    "B_vocab_expand":      rec["arms"]["B_expanded"]["terms"]}
            new = await asyncio.gather(*(ask(client, PROMPTS[n], q) for n in PROMPTS))
            for n, spec in zip(PROMPTS, new):
                arms[n] = spec.get("terms") or []
            res = {}
            for n, terms in arms.items():
                tq = tsquery(terms)
                rows = []
                if tq:
                    async with pool.acquire() as con:
                        rows = await con.fetch(SQL, tq)
                res[n] = {"terms": terms,
                          "hits": [{"id": r["identifier"], "title": r["title"],
                                    "abstract": r["abstract"]} for r in rows]}
            out[q] = res
            print(f"{q[:26]:28} " + "  ".join(f"{n.split('_')[0]}={len(res[n]['hits'])}" for n in arms), flush=True)
    await pool.close()
    json.dump(out, open(f"{D_}/factorial_raw.json", "w"), ensure_ascii=False, indent=1)

    # Which documents are NOT yet judged? Extend the pool properly this time.
    sheet = json.load(open(f"{D_}/judge_sheet.json"))
    known = {s["query"]: {c["id"] for c in s["candidates"]} for s in sheet}
    rng = random.Random(7); newsheet = []
    for q, res in out.items():
        fresh, seen = [], set()
        for n in res:
            for h in res[n]["hits"]:
                if h["id"] not in known[q] and h["id"] not in seen:
                    seen.add(h["id"]); fresh.append(h)
        rng.shuffle(fresh)
        if fresh:
            newsheet.append({"query": q, "candidates": [
                {"n": i+1, "id": h["id"], "title": h["title"], "abstract": h["abstract"]}
                for i, h in enumerate(fresh)]})
    json.dump(newsheet, open(f"{D_}/judge_sheet2.json", "w"), ensure_ascii=False, indent=1)
    print(f"\n待判新文档: {sum(len(s['candidates']) for s in newsheet)} 条,分布在 {len(newsheet)} 个 query")
asyncio.run(main())
