"""Build the pooled candidate set for the semantic-search judged relevance set.

    .venv/bin/python scripts/experiments/judgeset_pool.py

WHAT THIS IS FOR. Every retrieval number this project has published so far was measured on a
pool built from FTS arms only (docs/pg-notes/semsearch-ab.md). A vector arm scored against that
pool is penalised for every correct document FTS never surfaced -- the note says so itself:
"scoring it against the old pool counts them as irrelevant by default, a guaranteed
underestimate". So the pool has to be rebuilt with the vector arm inside it, which is what this
does: four arms, depth 10 each, deduplicated, shuffled, provenance stripped.

FOUR ARMS, AND WHY EACH ONE IS THERE.

  fts_body    Dutch FTS over `bekendmaking_search_text.activity_text` -- EXACTLY the text that
              was embedded. This is the primary baseline: against it, FTS-vs-vector varies the
              ranking function and nothing else.
  fts_title   The same LLM terms, same engine, over `title || abstract` -- the text the earlier
              experiments indexed. It exists to price the TEXT axis separately, because
              `abstract` is NULL on 22% of rows and a vector win over this arm could be the
              source text rather than the ranking.
  vec         The production path: `query: ` + the question through multilingual-e5-base, cosine
              over `bekendmaking_embedding`. Identical to what api/main/domains/search does.
  hybrid      Reciprocal rank fusion of fts_body and vec (k=60) over each arm's top 50. It is
              pooled rather than derived afterwards because RRF can lift a document from rank 40
              of one arm into its own top 10, i.e. from outside the depth-10 pool -- scoring it
              against a pool it helped build is the only way it is not handicapped in turn.

Both FTS arms consume the SAME LLM output. That is deliberate: term generation is held constant
so the arms differ only in which text the terms are matched against.

THE VOCABULARY IS MEASURED HERE, NOT PASTED. The expansion prompt that won the earlier A/B was
shown a vocabulary dump measured when the corpus was 61,853 rows from three municipalities. The
corpus is now 94,183 rows from 294, and its shape moved: `evenementenvergunning` went from a
minor type to the largest one (37%), and `TVM parkeervak` -- the top abstract prefix then, at
12,301 rows -- is an Amsterdam template that no longer leads. Feeding the baseline a stale
vocabulary would handicap it, and a handicapped baseline is not a baseline.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import random
import re
import sys

import asyncpg
import httpx

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "pg-notes"
for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

DSN = os.environ["DATABASE_URL"]
KEY = os.environ["DEEPSEEK_API_KEY"]

MODEL = "intfloat/multilingual-e5-base"
QUERY_PREFIX = "query: "
POOL_DEPTH = 10      # judged depth per arm
FUSE_DEPTH = 50      # how deep RRF looks before taking its own top 10
RRF_K = 60

# 40 questions a resident of a Dutch city could actually ask. The first 15 are carried over
# verbatim from docs/pg-notes/semsearch-ab.md so the two experiments share a spine; the other 25
# widen the intent coverage, mostly into the event side of the corpus, which barely existed when
# that set was written (9 municipalities then, 294 now, and events are 37% of the rows).
#
# WHY 40 AND NOT 15. The factorial follow-up computed the answer: with the measured per-query
# sd, 15 queries can only resolve a difference of ~25 percentage points, and 79 are needed for
# 17. 40 does not reach 79 -- it is what one judging session can honestly label -- and the score
# script prints the effect size this set can actually resolve rather than leaving the reader to
# assume any gap is real. Judgments are cached per (query, document), so growing the set later
# costs only the new pairs.
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
    "周末有没有集市或者跳蚤市场",
    "附近有音乐节或者露天演出吗",
    "有没有马拉松或者骑行比赛要封路",
    "哪条路要封闭施工",
    "有人申请夜间施工许可吗",
    "附近有放烟花的活动吗",
    "有人要开民宿或者短租公寓吗",
    "附近有儿童游乐场要改建吗",
    "有人申请摆露天座位的许可吗",
    "附近有加装太阳能板的申请吗",
    "有没有关于养狗或者遛狗区域的规定",
    "附近有马戏团或者游乐场来吗",
    "有人要在运河或者水上办活动吗",
    "附近有新的住宅开发项目吗",
    "有没有关于限速或者单行道的交通决定",
    "有人申请安装通信基站或者天线吗",
    "附近有教堂或者宗教活动的许可吗",
    "有人要砍掉一整片树林吗",
    "附近有酒类销售许可的申请吗",
    "有没有要征求居民意见的规划方案",
    "有人申请在人行道上搭脚手架吗",
    "附近有圣诞市场或者节日灯饰吗",
    "有人要改建成办公楼或者商铺吗",
    "附近有体育场馆的活动许可吗",
    "有没有关于噪音扰民的特别规定",
]

VOCAB_SQL = {
    "doc_type": "SELECT doc_type AS v, count(*) n FROM bekendmaking GROUP BY 1 ORDER BY n DESC",
    "activiteit": """SELECT a AS v, count(*) n FROM bekendmaking,
                     unnest(coalesce(activiteit, '{}')) a GROUP BY 1 ORDER BY n DESC""",
    "prefix": """SELECT left(activity_text, 40) AS v, count(*) n FROM bekendmaking_search_text
                 GROUP BY 1 ORDER BY n DESC LIMIT 40""",
}


async def vocabulary(con) -> str:
    """The prompt's view of the corpus, read off the corpus at run time."""
    parts = []
    for name, sql in VOCAB_SQL.items():
        rows = await con.fetch(sql)
        listed = ", ".join(f"{r['v']} ({r['n']})" for r in rows if r["v"])
        parts.append(f"{name} values with row counts:\n{listed}")
    return "\n\n".join(parts)


PROMPT = """You are building a full-text search query against a corpus of Dutch municipal
publications. Below is the ACTUAL vocabulary of that corpus, measured from the data just now.
Expand the resident's question into the terms that really occur in this corpus -- including
jargon and abbreviations -- not a literal translation.

%s

Output ONLY a JSON object: {"terms": ["...", "..."], "doc_type": [...], "activiteit": [...]}
Give 3-12 terms. doc_type and activiteit are optional hard filters; leave them as empty lists
unless the question clearly implies one."""


async def ask(client, system, question):
    r = await client.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {KEY}"},
        json={"model": "deepseek-chat", "temperature": 0,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": question}]},
        timeout=120.0,
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


def tsquery(terms) -> str | None:
    """Terms -> one tsquery. Multi-word terms become phrase queries, as in semsearch_ab.py."""
    parts = []
    for t in terms:
        t = re.sub(r"[^\w\s-]", " ", str(t).lower()).strip()
        words = [w for w in t.split() if w]
        if words:
            parts.append(" <-> ".join(words))
    return " | ".join(dict.fromkeys(parts)) if parts else None


# `b.doc_type`/`b.activiteit` filters are applied in both FTS arms because the measured best arm
# had them (+0.93 hits/query, best of five). The vector arm gets no filter: it has no LLM call to
# produce one, and inventing one for it would be a second axis.
FTS_BODY = """
SELECT t.identifier, ts_rank_cd(to_tsvector('dutch', t.activity_text), q.tq) AS score
FROM bekendmaking_search_text t
JOIN bekendmaking b USING (identifier), to_tsquery('dutch', $1) q(tq)
WHERE to_tsvector('dutch', t.activity_text) @@ q.tq
  AND ($2::text[] IS NULL OR cardinality($2::text[]) = 0 OR b.doc_type = ANY($2::text[]))
  AND ($3::text[] IS NULL OR cardinality($3::text[]) = 0 OR b.activiteit && $3::text[])
ORDER BY score DESC, b.published_at DESC
LIMIT $4
"""

FTS_TITLE = """
SELECT b.identifier,
       ts_rank_cd(to_tsvector('dutch', coalesce(b.title,'') || ' ' || coalesce(b.abstract,'')),
                  q.tq) AS score
FROM bekendmaking b, to_tsquery('dutch', $1) q(tq)
WHERE to_tsvector('dutch', coalesce(b.title,'') || ' ' || coalesce(b.abstract,'')) @@ q.tq
  AND ($2::text[] IS NULL OR cardinality($2::text[]) = 0 OR b.doc_type = ANY($2::text[]))
  AND ($3::text[] IS NULL OR cardinality($3::text[]) = 0 OR b.activiteit && $3::text[])
ORDER BY score DESC, b.published_at DESC
LIMIT $4
"""

VEC = """
SELECT e.identifier, 1 - (e.embedding <=> $1::vector) AS score
FROM bekendmaking_embedding e
ORDER BY e.embedding <=> $1::vector
LIMIT $2
"""

# THE VECTOR ARM IS MEASURED EXACTLY, WITH THE HNSW INDEX TURNED OFF, and that is not a detail.
# This judged set is an instrument for the question "does ranking by embedding beat ranking by
# ts_rank_cd". An approximate index answers a different question -- "how much of that ranking
# survives an ANN scan" -- which is already measured in docs/pg-notes/semantic-search.md
# (recall@20 was 72.5% at the default ef_search). Leaving the index on would silently fold the
# index's 27% loss into the model's score and understate the model.
#
# It also could not have worked: pgvector returns at most `ef_search` candidates per scan, and
# ef_search defaults to 40 while FUSE_DEPTH is 50 -- the arm would have been short rows before
# any ranking question was asked. Raising ef_search would not fix it either, because pgvector's
# cost estimate rises with it until the planner abandons the index anyway (measured; it is how
# an earlier ef sweep turned into eight sequential scans that looked like eight index scans).
#
# So: disable index scans for this connection and VERIFY the plan rather than trusting the GUC.
# `SET LOCAL` outside a transaction is a silent no-op -- also measured, also the hard way.
VEC_EXACT_SETUP = "SET enable_indexscan = off"
VEC_PLAN_CHECK = "EXPLAIN (FORMAT JSON) " + VEC


async def verify_exact(con, qvec: str) -> str:
    """Return the plan's node type, so 'exact' is a checked fact and not a comment."""
    plan = json.loads(await con.fetchval(VEC_PLAN_CHECK, qvec, FUSE_DEPTH))
    node = plan[0]["Plan"]
    while "Plans" in node:
        node = node["Plans"][0]
    return node["Node Type"]

# Shown to the judge. Deliberately NOT activity_text alone and NOT title alone: judging has to
# see what a resident would see in the result list, and withholding either field would make a
# document look irrelevant because of what the sheet omitted rather than what the document says.
DISPLAY = """
SELECT b.identifier, b.title, b.doc_type, b.gemeente, b.published_at,
       coalesce(t.activity_text, '') AS activity_text
FROM bekendmaking b
LEFT JOIN bekendmaking_search_text t USING (identifier)
WHERE b.identifier = ANY($1::text[])
"""


def rrf(*rankings: list[str]) -> list[str]:
    score: dict[str, float] = {}
    for ranking in rankings:
        for i, ident in enumerate(ranking):
            score[ident] = score.get(ident, 0.0) + 1.0 / (RRF_K + i + 1)
    return sorted(score, key=lambda k: -score[k])


async def main() -> None:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL)

    con = await asyncpg.connect(DSN)
    await con.execute(VEC_EXACT_SETUP)
    probe = model.encode(QUERY_PREFIX + "test", normalize_embeddings=True)
    node = await verify_exact(con, "[" + ",".join(f"{x:.6f}" for x in probe) + "]")
    print(f"vector arm plan: {node}")
    if "Index" in node:
        raise SystemExit("refusing to run: the vector arm is using an index, not an exact scan")

    vocab = await vocabulary(con)
    prompt = PROMPT % vocab
    (OUT / "judgeset-vocab.txt").write_text(vocab)

    records = []
    async with httpx.AsyncClient() as client:
        for n, q in enumerate(QUERIES, 1):
            spec = await ask(client, prompt, q)
            terms = spec.get("terms") or []
            tq = tsquery(terms)
            dt = spec.get("doc_type") or []
            act = spec.get("activiteit") or []

            vec = model.encode(QUERY_PREFIX + q, normalize_embeddings=True)
            qvec = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"

            body = await con.fetch(FTS_BODY, tq, dt, act, FUSE_DEPTH) if tq else []
            title = await con.fetch(FTS_TITLE, tq, dt, act, FUSE_DEPTH) if tq else []
            vrows = await con.fetch(VEC, qvec, FUSE_DEPTH)

            arms = {
                "fts_body": [r["identifier"] for r in body],
                "fts_title": [r["identifier"] for r in title],
                "vec": [r["identifier"] for r in vrows],
            }
            arms["hybrid"] = rrf(arms["fts_body"], arms["vec"])
            scores = {
                "fts_body": {r["identifier"]: float(r["score"]) for r in body},
                "fts_title": {r["identifier"]: float(r["score"]) for r in title},
                "vec": {r["identifier"]: float(r["score"]) for r in vrows},
            }
            records.append({"query": q, "terms": terms, "tsquery": tq,
                            "doc_type": dt, "activiteit": act,
                            "arms": arms, "scores": scores})
            print(f"[{n:2d}/{len(QUERIES)}] {q}  body={len(body)} title={len(title)} "
                  f"vec={len(vrows)}", flush=True)

    # The pool: depth POOL_DEPTH from every arm, deduplicated.
    pooled = {}
    for rec in records:
        ids, seen = [], set()
        for arm in ("fts_body", "fts_title", "vec", "hybrid"):
            for ident in rec["arms"][arm][:POOL_DEPTH]:
                if ident not in seen:
                    seen.add(ident)
                    ids.append(ident)
        pooled[rec["query"]] = ids

    wanted = sorted({i for ids in pooled.values() for i in ids})
    display = {r["identifier"]: dict(r) for r in await con.fetch(DISPLAY, wanted)}
    await con.close()

    (OUT / "judgeset-raw.json").write_text(json.dumps(
        {"queries": records, "pool_depth": POOL_DEPTH, "fuse_depth": FUSE_DEPTH,
         "rrf_k": RRF_K, "model": MODEL}, ensure_ascii=False, indent=1))

    # The blind judging sheet. Shuffled with a fixed seed and stripped of every trace of which
    # arm produced a candidate, including its score and its rank -- the order on the sheet must
    # carry no information, or judging drifts toward whichever system put a document first.
    rng = random.Random(20261105)
    sheet = []
    for rec in records:
        ids = list(pooled[rec["query"]])
        rng.shuffle(ids)
        sheet.append({
            "query": rec["query"],
            "candidates": [{
                "n": i + 1, "id": ident,
                "title": display[ident]["title"],
                "doc_type": display[ident]["doc_type"],
                "gemeente": display[ident]["gemeente"],
                "text": display[ident]["activity_text"][:300],
            } for i, ident in enumerate(ids)],
        })
    (OUT / "judgeset-judge_sheet.json").write_text(json.dumps(sheet, ensure_ascii=False, indent=1))

    total = sum(len(s["candidates"]) for s in sheet)
    print(f"\npool: {total} (query, document) pairs over {len(sheet)} queries "
          f"({total / len(sheet):.1f} per query)")
    print(f"wrote judgeset-raw.json, judgeset-judge_sheet.json, judgeset-vocab.txt to {OUT}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
