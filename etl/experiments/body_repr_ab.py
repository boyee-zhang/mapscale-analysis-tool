"""Arm A/D: does embedding the BODY order relevant documents better than the 23-token summary?

    .venv/bin/python -m etl.experiments.body_repr_ab [--limit N] [--distractors N]

THE GATE, AND WHY IT IS SHAPED LIKE THIS. Production embeds
`bekendmaking_activity.activity_text`, a derived keyword summary: median **23 tokens** against
multilingual-e5-base's 512-token window, while `bekendmaking_body.body_text` -- median **452
tokens**, present on 94,183 of 94,183 publications -- is read by nothing. Switching is a
~5-hour encode and ~670 MB of index, and the frozen judgeset cannot score the result (its pool
is the depth-10 union of four arms, so every document a new arm surfaces from outside it is
scored 0: a guaranteed underestimate, measured at 273 new judgments for a far smaller change).

So this script does not retrieve. It RERANKS THE ALREADY-JUDGED POOL under the two
representations. Every document it ranks carries a grade, so there is no fill convention, no
bound and no pool bias -- which is the entire reason this runs before anything is built.

WHAT IT CAN AND CANNOT SHOW, stated here because the write-up must repeat it. Reranking a fixed
set measures ORDERING; the problem is RECALL. This is a NECESSARY condition, not a sufficient
one: an arm that cannot order a 27-document enriched pool better will not order 94,183 better,
but an arm that can has still not been shown to surface anything from OUTSIDE the pool, which
is the actual win.

To make it recall-shaped at near-zero cost, a shared set of random corpus documents is added as
presumed-irrelevant distractors, turning each query's pool into a mini-corpus. The presumption
is conservative -- a distractor that is genuinely relevant can only penalise the arm that ranks
it highly, never inflate it -- and the scorer reports how many distractors each arm puts in its
top-10, so a large count gets judged rather than assumed. The set is SHARED across queries
rather than drawn per query: the same distractors for every query makes the two arms paired on
them, and it costs 2,000 encodings instead of 8,000.

PRECONDITION ALREADY MEASURED, AND IT CUTS BOTH WAYS (2026-11-18). Of the 169 pool-best
documents `vec` misses at depth 10, 167 have more body text than embedded text (median 1,249 vs
165). But those missed documents already carried 3.4x MORE embedded text than the 178 it finds
(median 165 vs 48), so thin representation does NOT explain the misses and this arm is not
guaranteed to fix them. That asymmetry is why this is a gate and not a formality.

TRUNCATION IS BY TOKENS, NOT CHARACTERS, and that is a decision. `etl/derive/embeddings.py`
applies `MAX_CHARS = 1800`, which never bites at a median of 23 tokens; on body text it would
cut at a language-dependent position mid-token. Here the model's own 512-token truncation is
used and each document is FLAGGED as truncated or not, because "the gain lives only in the
56.1% that fit whole" and "the gain lives only in the truncated 43.9%" are different findings
and one of them means the truncation is wrong.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import random

import asyncpg
import numpy as np

from ..core import db

OUT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "pg-notes"
RAW = OUT / "raw"
MODEL = "intfloat/multilingual-e5-base"
MAX_TOKENS = 512
BATCH = 64
DEPTH = 50
DISTRACTORS = 2000
SEED = 20261118

# Both prefixes are read from the production module rather than retyped. `embeddings.py`'s own
# docstring is the reason: documents are "passage: " and queries are "query: ", and mixing them
# degrades retrieval with no error anywhere.
# No sys.path.insert: `-m` from the repo root already puts the repo root on sys.path, which is
# what retired ten of these in the etl/ refactor. core.db is imported first, deliberately --
# it anchors load_dotenv to the repo root, while api.main.config calls a bare load_dotenv()
# that resolves against the working directory.
from api.main.domains.search.encoder import QUERY_PREFIX
from etl.derive.embeddings import MAX_CHARS, PASSAGE_PREFIX

# The pooled documents, their grades, and the production vector to reproduce.
FETCH_DOCS = """
SELECT a.identifier, a.activity_text, coalesce(bb.body_text, '') AS body_text,
       e.text_sha1 AS prod_sha1, e.embedding::text AS prod_vec
FROM bekendmaking_activity a
LEFT JOIN bekendmaking_body bb USING (identifier)
LEFT JOIN bekendmaking_embedding e USING (identifier)
WHERE a.identifier = ANY($1::text[])
"""

# Distractors: a deterministic sample over the whole corpus, excluding nothing -- a draw that
# happens to land on a judged document is resolved in favour of its grade by the scorer.
FETCH_DISTRACTORS = """
SELECT a.identifier, a.activity_text, coalesce(bb.body_text, '') AS body_text
FROM bekendmaking_activity a
LEFT JOIN bekendmaking_body bb USING (identifier)
WHERE a.activity_derived AND a.activity_text <> ''
ORDER BY md5(a.identifier || $2::text)
LIMIT $1
"""


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def text_act(activity_text: str) -> str:
    """Byte-identical to etl/derive/embeddings.py's encode_text -- the control arm IS production."""
    return PASSAGE_PREFIX + (activity_text or "")[:MAX_CHARS]


def text_body(body_text: str, activity_text: str) -> str:
    """The body, with the summary as the fallback where there is no body at all.

    Falling back rather than embedding the empty string keeps the arm a representation change
    and not a deletion: a publication with no body would otherwise become unretrievable, which
    would show up as a recall loss that has nothing to do with the representation.
    """
    return PASSAGE_PREFIX + (body_text or activity_text or "")


def rank(doc_mat: np.ndarray, ids: np.ndarray, qvec: np.ndarray, depth: int):
    """Top-`depth` by cosine, ties broken by identifier.

    np.lexsort, not argpartition: a partition only guarantees the k-th position, so a tie group
    straddling the cut can lose members to an arbitrary pivot. Pre-registered, same rule as the
    title-prefix experiment.
    """
    scores = doc_mat @ qvec
    order = np.lexsort((ids, -scores))[:depth]
    return [(str(ids[i]), float(scores[i])) for i in order]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="first N queries only (smoke test)")
    ap.add_argument("--distractors", type=int, default=DISTRACTORS)
    args = ap.parse_args()

    judged = json.loads((OUT / "judgeset-judgments.json").read_text())
    queries = list(judged)[: args.limit] if args.limit else list(judged)
    pooled = sorted({d for q in queries for d in judged[q]})
    print(f"{len(queries)} queries, {len(pooled)} distinct judged documents, "
          f"{sum(len(judged[q]) for q in queries)} (query, document) pairs")

    conn = await asyncpg.connect(db.dsn())
    try:
        docs = {r["identifier"]: dict(r) for r in await conn.fetch(FETCH_DOCS, pooled)}
        dis = [dict(r) for r in await conn.fetch(FETCH_DISTRACTORS, args.distractors, str(SEED))]
    finally:
        await conn.close()
    missing = [d for d in pooled if d not in docs]
    if missing:
        raise SystemExit(f"{len(missing)} judged documents are not in the database: {missing[:5]}")
    print(f"{len(dis)} distractors drawn (seed {SEED}); "
          f"{len({d['identifier'] for d in dis} & set(pooled))} of them are also judged")

    # ---- C1: the control arm's text must be the text production actually embedded.
    #
    # It re-encodes rather than reading `bekendmaking_embedding`, so a stale stored vector does
    # not invalidate the comparison -- but it is reported by name, because a large mismatch
    # count means the production table is stale and any claim about "the production arm" needs
    # that said out loud. One row (gmb-2026-317120) failed this in the title-prefix experiment,
    # which is how we know the check can fail.
    mism = [i for i, d in docs.items()
            if d["prod_sha1"] is not None and sha1(text_act(d["activity_text"])) != d["prod_sha1"]]
    nover = [i for i, d in docs.items() if d["prod_sha1"] is None]
    print(f"C1 control-arm text vs stored text_sha1: "
          f"{len(docs) - len(mism) - len(nover)}/{len(docs)} match, "
          f"{len(mism)} stale, {len(nover)} never embedded")
    if mism:
        print(f"   stale: {mism[:10]}")
    if len(mism) > 0.01 * len(docs):
        raise SystemExit("C1: more than 1% of the judged pool has a stale production vector; "
                         "re-run etl/derive/embeddings.py before measuring against it")

    from sentence_transformers import SentenceTransformer
    from transformers import AutoTokenizer
    model = SentenceTransformer(MODEL)
    model.max_seq_length = MAX_TOKENS
    tok = AutoTokenizer.from_pretrained(MODEL)
    print(f"{MODEL}, max_seq_length={model.max_seq_length}, device={model.device}")

    all_docs = list(docs.values()) + [d for d in dis if d["identifier"] not in docs]
    ids = np.array([d["identifier"] for d in all_docs], dtype=object)
    t_act = [text_act(d["activity_text"]) for d in all_docs]
    t_body = [text_body(d["body_text"], d["activity_text"]) for d in all_docs]

    # Truncation is recorded per document, not inferred from a character count.
    ntok = [len(tok(t, add_special_tokens=True)["input_ids"]) for t in t_body]
    trunc = {str(i): bool(n > MAX_TOKENS) for i, n in zip(ids, ntok)}
    print(f"body token length: median {int(np.median(ntok))}, "
          f"truncated at {MAX_TOKENS}: {sum(trunc.values())}/{len(ntok)} "
          f"({sum(trunc.values()) / len(ntok):.1%})")

    def encode(texts, label):
        print(f"  encoding {label} ({len(texts)} texts)...", flush=True)
        return model.encode(texts, batch_size=BATCH, normalize_embeddings=True,
                            show_progress_bar=False)

    M = {"act": encode(t_act, "act"), "body": encode(t_body, "body")}
    qv = encode([QUERY_PREFIX + q for q in queries], "queries")

    # ---- C1b: the real reproduction control. Our re-encoded `act` vector must agree with the
    # production vector for the rows whose text matched. This can fail on a wrong prefix, a
    # missing normalize_embeddings, or a different model -- none of which C1 would notice.
    ok_rows = [(n, d) for n, d in enumerate(all_docs)
               if d.get("prod_vec") and d["identifier"] not in set(mism)]
    cos = [float(M["act"][n] @ np.fromstring(d["prod_vec"].strip("[]"), sep=","))
           for n, d in ok_rows[:500]]
    print(f"C1b re-encoded act vs stored production vector, n={len(cos)}: "
          f"min cosine {min(cos):.6f}, median {np.median(cos):.6f}")
    if min(cos) < 0.999:
        raise SystemExit("C1b: the control arm does not reproduce the production vector; "
                         "the comparison would not be about the representation")

    idx = {str(i): n for n, i in enumerate(ids)}
    out = {"model": MODEL, "max_tokens": MAX_TOKENS, "depth": DEPTH, "seed": SEED,
           "distractors": sorted({d["identifier"] for d in dis} - set(docs)),
           "truncated": trunc, "stale_prod_vectors": mism, "queries": []}
    for n, q in enumerate(queries):
        pool_ids = sorted(judged[q])
        pool_rows = np.array([idx[i] for i in pool_ids])
        mini_rows = np.arange(len(ids))
        rec = {"query": q, "arms": {}, "arms_mini": {}}
        for arm, mat in M.items():
            rec["arms"][arm] = rank(mat[pool_rows], ids[pool_rows], qv[n], DEPTH)
            rec["arms_mini"][arm] = rank(mat[mini_rows], ids[mini_rows], qv[n], DEPTH)
        out["queries"].append(rec)
        print(f"  [{n + 1}/{len(queries)}] {q}")

    RAW.mkdir(parents=True, exist_ok=True)
    dest = RAW / "body-repr-ab-raw.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    random.seed(SEED)
    raise SystemExit(asyncio.run(main()))
