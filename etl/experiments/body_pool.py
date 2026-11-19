"""Pool the BODY arm into the judged set, so the 38x out-of-pool rate becomes a signed number.

    .venv/bin/python -m etl.experiments.body_pool              # encode (cached) + build the sheet
    .venv/bin/python -m etl.experiments.body_pool --encode-only
    .venv/bin/python -m etl.experiments.body_pool --limit 3    # smoke test, 3 queries

WHY THIS EXISTS, AND WHY IT IS NOT A BIGGER JUDGESET. The Phase 0 gate
(`docs/pg-notes/semsearch-body-repr.md`) reranked each query's already-judged pool and found no
measurable ordering difference -- mean +0.088 against this set's own MDE of 0.133. Two limits
produced that, and they are NOT the same limit:

  RESOLUTION  40 queries at a per-query sd of 0.302 can only resolve 0.134 nDCG. Fixing that
              needs ~93 QUERIES (measured, not guessed: 2.8*sd/sqrt(n)), i.e. ~1,500 more
              judgments, and it still answers an ORDERING question.
  SHAPE       the pool is the depth-10 union of four arms, so every document a new arm surfaces
              from outside it is scored 0. RECALL is untestable here by construction -- 1,000
              queries would not help. The only fix is to put the new arm INSIDE the pooling.

So this does the second, which is ~12x cheaper than the first and answers the question that is
actually open. `judgeset_pool.py` is left untouched: re-running it needs a DEEPSEEK key, costs
40 LLM calls, and would overwrite `judgeset-raw.json` / `judgeset-judge_sheet.json`, which are
pinned by sha256 in `evidence` rows and are inside the `judgeset` path facet. This script only
ADDS, and it writes to its own files.

NOTHING IS BUILT IN THE DATABASE. No migration, no `bekendmaking_chunk_embedding`, no HNSW
index, no change under `api/`. The arm is 94,183 x 768 float32 in a gitignored `data/` file
(289 MB) and 40 brute-force matmuls. Measured 2026-11-19 on this machine: 22.3 docs/s on
mps, so ~70 minutes for the corpus -- NOT the ~5 hours the plan estimated from the
title-prefix arm's token count. That is also why this step does not need the Phase 0 gate to
have passed: it puts nothing into the live path.

EXACT, NOT APPROXIMATE, and for the same reason `judgeset_pool.py` turns the HNSW index off:
an ANN scan would fold its own recall loss into the arm's score. A full matmul over 94,183
rows is milliseconds, so there is no reason to approximate.

WHAT THE JUDGE IS AND IS NOT TOLD. The sheet is shuffled with a fixed seed and carries no
score, no rank, no arm and no grade. 10 CALIBRATION ITEMS drawn from pairs already judged on
2026-11-13 are mixed in, in the identical field shape, so judge drift between the two sessions
is measurable rather than assumed; their grades live in a SEPARATE answer-key file so the sheet
cannot unblind itself.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import random
import time

import asyncpg
import numpy as np

from ..core import db
from .body_repr_ab import MODEL, sha1, text_body

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "pg-notes"
CACHE = ROOT / "data" / "body_vectors.npz"   # data/ is gitignored; 289 MB

MAX_TOKENS = 512
BATCH = 64
POOL_DEPTH = 10          # the same depth judgeset_pool.py pooled its four arms at
CALIBRATION = 10
SEED = 20261119

# Every publication retrieval can reach, in a stable order. `activity_derived AND
# activity_text <> ''` is `etl/derive/embeddings.py`'s own filter, so the body arm's candidate
# set is the same population as the production arm's -- otherwise a recall difference could be
# a population difference.
FETCH_CORPUS = """
SELECT a.identifier, a.activity_text, coalesce(bb.body_text, '') AS body_text
FROM bekendmaking_activity a
LEFT JOIN bekendmaking_body bb USING (identifier)
WHERE a.activity_derived AND a.activity_text <> ''
ORDER BY a.identifier
"""

# What the judge sees. Identical to judgeset_pool.py's DISPLAY, deliberately: a candidate shown
# with different fields than the 2026-11-13 sheet would make the calibration items a different
# task from the ones they calibrate against.
DISPLAY = """
SELECT b.identifier, b.title, b.doc_type, b.gemeente, b.published_at,
       coalesce(t.activity_text, '') AS activity_text
FROM bekendmaking b
LEFT JOIN bekendmaking_search_text t USING (identifier)
WHERE b.identifier = ANY($1::text[])
"""


def load_model():
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL)
    model.max_seq_length = MAX_TOKENS
    return model


async def encode_corpus(force: bool) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Encode every body, incrementally by content hash. Returns (ids, vectors) aligned.

    Incremental by sha1 of the EXACT encoded text, which is `embeddings.py`'s design and its
    recorded reason: `activity_text` is a view column reassembled on read, so a vector keyed on
    identifier alone cannot say whether it is still valid -- `data/noise_emb_cache.npz` is the
    cache in this repo that got that wrong and had to be thrown away.
    """
    conn = await asyncpg.connect(db.dsn())
    try:
        rows = await conn.fetch(FETCH_CORPUS)
    finally:
        await conn.close()
    ids = np.array([r["identifier"] for r in rows], dtype=object)
    texts = [text_body(r["body_text"], r["activity_text"]) for r in rows]
    hashes = np.array([sha1(t) for t in texts], dtype=object)
    print(f"corpus: {len(ids)} publications")

    # A checkpointable cache: full-length arrays plus a `done` mask. A row that has not been
    # encoded holds a zero vector, and a zero vector must never be mistaken for a cached one --
    # the mask is what makes a partial file correct rather than quietly wrong. Keyed on
    # (identifier, sha1 of the exact encoded text), which is `embeddings.py`'s design and its
    # recorded reason: `data/noise_emb_cache.npz` keyed on identifier alone could not say which
    # of its rows were still valid and had to be thrown away.
    vectors = np.zeros((len(ids), 768), dtype=np.float32)
    done = np.zeros(len(ids), dtype=bool)
    if CACHE.exists() and not force:
        z = np.load(CACHE, allow_pickle=True)
        # Every member is read ONCE into a local. `z[...]` on an NpzFile re-reads that member
        # out of the archive on each access, so leaving `z["vectors"][src]` inside the loop
        # below means re-reading a 295 MB member 94,183 times -- measured by it not finishing
        # in ten minutes, which is the only way this kind of thing announces itself.
        z_ids, z_hashes, z_done, z_vec = z["ids"], z["hashes"], z["done"], z["vectors"]
        have = {f"{i}:{h}": n for n, (i, h, d) in
                enumerate(zip(z_ids, z_hashes, z_done)) if d}
        for n, (i, h) in enumerate(zip(ids, hashes)):
            src = have.get(f"{i}:{h}")
            if src is not None:
                vectors[n] = z_vec[src]
                done[n] = True
        print(f"cache: {CACHE.name}, {len(have)} vectors, {int(done.sum())} reusable here")

    todo = [n for n in range(len(ids)) if not done[n]]
    print(f"to encode: {len(todo)} ({len(ids) - len(todo)} reused)")

    def checkpoint():
        np.savez(CACHE, ids=ids, hashes=hashes, vectors=vectors, done=done)

    if todo:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        model = load_model()
        print(f"{MODEL}, max_seq_length={model.max_seq_length}, device={model.device}")
        t0 = time.perf_counter()
        for start in range(0, len(todo), 2048):
            block = todo[start:start + 2048]
            vectors[block] = model.encode([texts[n] for n in block], batch_size=BATCH,
                                          normalize_embeddings=True, show_progress_bar=False)
            done[block] = True
            checkpoint()
            seen = start + len(block)
            el = time.perf_counter() - t0
            print(f"  {seen}/{len(todo)}  {seen / el:.1f} docs/s  "
                  f"eta {(len(todo) - seen) / (seen / el) / 60:.0f} min", flush=True)
        print(f"wrote {CACHE} ({CACHE.stat().st_size / 1e6:.0f} MB)")
    return ids, vectors, texts


def control_alignment(ids, vectors, texts_of, rng) -> None:
    """C1: the cached vector at row n must be the encoding of the document at row n.

    This can fail -- a cache written in a different row order, or a stale hash map, both land
    here and nowhere else, and both would silently make the arm's rankings belong to other
    documents. Sabotage check: rolling `vectors` by one row makes it FAIL.
    """
    sample = rng.sample(range(len(ids)), 32)
    model = load_model()
    fresh = model.encode([texts_of[n] for n in sample], batch_size=BATCH,
                         normalize_embeddings=True, show_progress_bar=False)
    cos = [float(fresh[k] @ vectors[n]) for k, n in enumerate(sample)]
    print(f"C1 cached vector vs re-encoded, n=32: min cosine {min(cos):.6f}")
    if min(cos) < 0.9999:
        raise SystemExit("C1: the cache is not aligned with the corpus rows; "
                         "re-run with --force-encode")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="first N queries only (smoke test)")
    ap.add_argument("--encode-only", action="store_true")
    ap.add_argument("--force-encode", action="store_true")
    ap.add_argument("--skip-control", action="store_true",
                    help="skip C1 (it loads the model; only for a re-run minutes later)")
    args = ap.parse_args()

    ids, vectors, texts = await encode_corpus(args.force_encode)
    if args.encode_only:
        return 0

    # The 40 queries come from the judgments file, not from judgeset_pool.QUERIES: that is the
    # set that was actually judged, and importing that module would require a DEEPSEEK key.
    judged = json.loads((OUT / "judgeset-judgments.json").read_text())
    queries = list(judged)[: args.limit] if args.limit else list(judged)
    print(f"{len(queries)} queries, {sum(len(v) for v in judged.values())} existing judgments")

    rng = random.Random(SEED)
    if not args.skip_control:
        control_alignment(ids, vectors, texts, rng)

    from api.main.domains.search.encoder import QUERY_PREFIX
    model = load_model()
    qv = model.encode([QUERY_PREFIX + q for q in queries], batch_size=BATCH,
                      normalize_embeddings=True, show_progress_bar=False)

    # Exact top-POOL_DEPTH, ties broken by identifier -- the pre-registered rule, a full
    # lexsort and never argpartition, which only guarantees the k-th position and can drop
    # members of a tie group straddling the cut.
    arm: dict[str, list[str]] = {}
    for n, q in enumerate(queries):
        scores = vectors @ qv[n]
        order = np.lexsort((ids, -scores))[:POOL_DEPTH]
        arm[q] = [str(ids[i]) for i in order]

    new_pairs = [(q, d) for q in queries for d in arm[q] if d not in judged.get(q, {})]
    overlap = sum(len(arm[q]) for q in queries) - len(new_pairs)
    print(f"body arm top-{POOL_DEPTH}: {len(new_pairs)} new (query, document) pairs, "
          f"{overlap} already judged (cached, free)")

    # C2: a "new" pair that is already judged would be paid for twice and would also silently
    # overwrite a grade from the earlier session.
    assert not [p for p in new_pairs if p[1] in judged.get(p[0], {})], "C2: pair already judged"

    # C4: the arm must actually differ from the production arm, or there is nothing to judge
    # and the 38x finding was an artefact of the mini-corpus.
    raw = json.loads((OUT / "judgeset-raw.json").read_text())
    prod = {r["query"]: r["arms"]["vec"][:POOL_DEPTH] for r in raw["queries"]}
    same = [q for q in queries if arm[q] == prod.get(q)]
    ov = [len(set(arm[q]) & set(prod.get(q, []))) for q in queries]
    print(f"C4 vs production vec top-{POOL_DEPTH}: {len(same)}/{len(queries)} identical, "
          f"mean overlap {np.mean(ov):.2f}/{POOL_DEPTH}")
    if len(same) == len(queries):
        raise SystemExit("C4: the body arm reproduces the production arm exactly; "
                         "there is no new candidate set and nothing to judge")

    # Calibration: already-judged pairs, stratified over the three grades, spread across
    # queries. They measure drift between the 2026-11-13 session and this one.
    by_grade: dict[int, list[tuple[str, str]]] = {0: [], 1: [], 2: []}
    for q in queries:
        for d, g in judged[q].items():
            if d not in arm[q]:
                by_grade[int(g)].append((q, d))
    want = {2: 4, 1: 3, 0: 3}
    calib = [p for g, k in want.items() for p in rng.sample(by_grade[g], min(k, len(by_grade[g])))]
    print(f"calibration: {len(calib)} already-judged pairs "
          f"({ {g: sum(1 for q, d in calib if int(judged[q][d]) == g) for g in (0, 1, 2)} })")

    wanted = sorted({d for _, d in new_pairs + calib})
    conn = await asyncpg.connect(db.dsn())
    try:
        display = {r["identifier"]: dict(r) for r in await conn.fetch(DISPLAY, wanted)}
    finally:
        await conn.close()
    missing = [d for d in wanted if d not in display]
    if missing:
        raise SystemExit(f"{len(missing)} candidates have no display row: {missing[:5]}")

    sheet, key = [], {}
    for q in queries:
        items = [d for qq, d in new_pairs if qq == q] + [d for qq, d in calib if qq == q]
        if not items:
            continue
        rng.shuffle(items)
        sheet.append({"query": q, "candidates": [{
            "n": i + 1, "id": d,
            "title": display[d]["title"], "doc_type": display[d]["doc_type"],
            "gemeente": display[d]["gemeente"],
            "text": display[d]["activity_text"][:300],
        } for i, d in enumerate(items)]})
        for qq, d in calib:
            if qq == q:
                key[f"{q}\t{d}"] = int(judged[q][d])

    # C3: the sheet must carry no grade, no score, no rank and no arm, and a calibration item
    # must be indistinguishable in shape from a new one. A field that leaks provenance drifts
    # judging toward whichever system put a document first.
    fields = {k for s in sheet for c in s["candidates"] for k in c}
    assert fields == {"n", "id", "title", "doc_type", "gemeente", "text"}, f"C3: {fields}"
    assert all(len({c["id"] for c in s["candidates"]}) == len(s["candidates"]) for s in sheet), \
        "C3: duplicate candidate within a query"
    print(f"C3 sheet fields: {sorted(fields)} -- no grade, score, rank or arm")

    (OUT / "bodypool-arm.json").write_text(json.dumps(
        {"model": MODEL, "max_tokens": MAX_TOKENS, "pool_depth": POOL_DEPTH, "seed": SEED,
         "corpus_size": len(ids), "arm": arm,
         "new_pairs": new_pairs, "overlap_with_judged": overlap,
         "prod_overlap_mean": float(np.mean(ov))}, ensure_ascii=False, indent=1))
    (OUT / "bodypool-judge_sheet.json").write_text(json.dumps(sheet, ensure_ascii=False, indent=1))
    (OUT / "bodypool-calibration_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1))

    # The answer file, pre-keyed with nulls in the sheet's own order, so judging is replacing a
    # null with 0/1/2 rather than retyping 390 identifiers. It is NOT overwritten once it has a
    # grade in it: regenerating the sheet is cheap and idempotent, and silently resetting a
    # session's work to null would be the most expensive bug this script could have.
    ans = OUT / "bodypool-judgments.json"
    skeleton = {s_["query"]: {c["id"]: None for c in s_["candidates"]} for s_ in sheet}
    if ans.exists():
        prev = json.loads(ans.read_text())
        filled = sum(1 for v in prev.values() for g in v.values() if g is not None)
        if filled:
            print(f"{ans.name} already holds {filled} grades -- left untouched")
        else:
            ans.write_text(json.dumps(skeleton, ensure_ascii=False, indent=1))
    else:
        ans.write_text(json.dumps(skeleton, ensure_ascii=False, indent=1))

    total = sum(len(s["candidates"]) for s in sheet)
    print(f"\nsheet: {total} items over {len(sheet)} queries "
          f"({len(new_pairs)} to judge + {len(calib)} calibration)")
    print(f"wrote bodypool-arm.json, bodypool-judge_sheet.json, "
          f"bodypool-calibration_key.json, bodypool-judgments.json to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
