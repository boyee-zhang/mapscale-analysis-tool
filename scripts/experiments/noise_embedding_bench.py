"""Does an embedding beat keyword search on the noise concept, and does it add anything to the
metadata classifier? Two questions, deliberately answered apart.

They are separate because the run-1 notes conflated them and produced a misleading table.
`cls_works` scores F1 0.845 but 87% of its true positives come from two STRUCTURED fields
(`activiteit`, `doc_type`); only 13% come from matching text. An embedding is a text method,
so putting it next to 0.845 would repeat exactly that error.

  GOAL 1  text vs text.  Bar: the best text arm over the same three blocks, rules_re:ta at
          F1 0.475, and the retrieve-everything floor at 0.621 -- which NO text method has
          ever cleared. Unsupervised, because the keyword arms it is being compared with are:
          a human writes a description of what they want and the engine ranks documents.
  GOAL 2  value on top of metadata. cls_works retrieves 293 of 695 (256 noisy, 37 quiet) and
          leaves 402 behind (57 noisy, 345 quiet). So "does text add anything" decomposes into
          two ranking tasks with a known answer key: PRUNE the 37 false positives out of the
          retrieved set, and RECOVER the 57 noisy from the rejected set. A combined classifier
          under leave-one-block-out CV then says whether it is worth anything end to end.

Three things kept honest, in the same spirit as the FTS run:

1. NO THRESHOLD IS CHOSEN ON THE TEST DATA. A cosine score needs a cutoff to become a set, and
   picking the cutoff that maximises F1 on the labels is how embeddings win rigged benchmarks.
   Primary metrics are therefore threshold-free (ROC-AUC, average precision). Where an F1 is
   given for comparison with the keyword arms, the threshold comes from the OTHER blocks
   (leave-one-block-out), never from the block being scored. The best-threshold F1 is printed
   too, labelled `oracle`, purely as the ceiling the method cannot exceed.
2. SUPERVISED AND UNSUPERVISED ARMS ARE NOT MIXED. The keyword arms never saw a label, so only
   the query-similarity arms are a fair comparison for goal 1. Anything fitted on labels is
   marked `sup` and read separately.
3. THE QUERY TEXT COMES FROM THE TASK DEFINITION, not from reading the positives. The six
   clauses below are the `noisy` test in docs/pg-notes/noise-groundtruth.md, translated into
   Dutch. That is the same information the 0012 vocabulary census had, and strictly less than
   the labels.
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib

import asyncpg
import numpy as np
from dotenv import dotenv_values
from sentence_transformers import SentenceTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

# Read .env directly rather than through api.main.db, which is how the other scripts here get
# the DSN. This one cannot: torch and sentence-transformers are ~1.1 GB and must stay out of
# the venv this project deploys from (FastAPI on Vercel via Mangum), so it runs in a throwaway
# venv that does not have the application's dependencies. Importing api.main.db pulls in
# api.main.logger, which pulls in starlette -- the whole web stack, to read one string.
#
#   python3 -m venv /tmp/embvenv
#   /tmp/embvenv/bin/pip install sentence-transformers==3.4.1 scikit-learn==1.6.1 \
#       asyncpg python-dotenv
#   /tmp/embvenv/bin/python scripts/experiments/noise_embedding_bench.py --cache /tmp/emb.npz
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DATABASE_URL = dotenv_values(ROOT / ".env").get("DATABASE_URL")

MODEL = "intfloat/multilingual-e5-base"

# The `noisy` test, clause by clause, in the language the corpus is written in.
QUERIES = {
    "structuur": "constructieve werkzaamheden aan draagmuren, fundering, kelder, dakvlak of vloeren",
    "grondwerk": "graafwerkzaamheden, grondverzet of werkzaamheden in het water",
    "sloop": "sloopwerkzaamheden en het afbreken van een gebouw",
    "bomen": "het vellen of kappen van bomen",
    "herbouw": "het geheel verbouwen of herbouwen van een pand",
    "geluid": "versterkt geluid, muziek, optredens of een evenement met publiek",
}
# One sentence, for the user who types a single description rather than six.
QUERY_ONE = "werkzaamheden die veel geluidsoverlast veroorzaken voor omwonenden"

FIELDS = ("ta", "act", "body")

SQL = """
SELECT m.frame, b.identifier, l.label,
       coalesce(b.title,'') || ' ' || coalesce(b.abstract,'')        AS ta,
       a.activity_text                                               AS act,
       coalesce(bb.body_text, '')                                    AS body,
       b.doc_type, b.activiteit, n.impact
FROM noise_frame_member m
JOIN bekendmaking b USING (identifier)
JOIN bekendmaking_activity a USING (identifier)
JOIN noise_label l USING (identifier)
JOIN bekendmaking_noise n USING (identifier)
LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
ORDER BY m.frame, b.identifier
"""

CLS_WORKS = ("explicit", "loud_activity", "event", "undeterminable")


def f1_at(y, s, t):
    pred = s >= t
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    if tp == 0:
        return 0.0, 0.0, 0.0
    p, r = tp / (tp + fp), tp / (tp + fn)
    return p, r, 2 * p * r / (p + r)


def best_threshold(y, s):
    """The cutoff maximising F1 on THIS data. Only ever applied to other blocks."""
    cand = np.unique(s)
    if len(cand) > 400:
        cand = np.quantile(s, np.linspace(0, 1, 400))
    return max(cand, key=lambda t: f1_at(y, s, t)[2])


def loblo_f1(frames, y, s):
    """Leave-one-block-out F1: the threshold for each block comes from the other blocks."""
    out, pooled = {}, np.zeros(len(y), dtype=bool)
    for fr in sorted(set(frames)):
        te = frames == fr
        t = best_threshold(y[~te], s[~te])
        out[fr] = f1_at(y[te], s[te], t)
        pooled[te] = s[te] >= t
    tp = int((pooled & (y == 1)).sum())
    fp = int((pooled & (y == 0)).sum())
    fn = int((~pooled & (y == 1)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    out["POOLED"] = (p, r, 2 * p * r / (p + r) if p + r else 0.0)
    return out


def rank_report(name, frames, y, s, tuned="no"):
    auc, ap = roc_auc_score(y, s), average_precision_score(y, s)
    cv = loblo_f1(frames, y, s)
    orac = f1_at(y, s, best_threshold(y, s))[2]
    p, r, f = cv["POOLED"]
    print(f"{name:26}{tuned:5}{auc:7.3f}{ap:7.3f}{p:8.3f}{r:7.3f}{f:7.3f}{orac:8.3f}   "
          + " ".join(f"{k.split('-')[1][:6]}={cv[k][2]:.3f}" for k in sorted(cv) if k != "POOLED"))
    return f


async def load():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(SQL)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


def main() -> int:
    ap_ = argparse.ArgumentParser(description=__doc__)
    ap_.add_argument("--model", default=MODEL)
    ap_.add_argument("--cache", default=None, help="npz file to cache embeddings in")
    args = ap_.parse_args()

    rows = asyncio.run(load())
    rows = [r for r in rows if r["label"] in ("noisy", "quiet")]
    frames = np.array([r["frame"] for r in rows])
    y = np.array([1 if r["label"] == "noisy" else 0 for r in rows])
    print(f"strict universe: {len(rows)} rows, {int(y.sum())} noisy, "
          f"base rate {y.mean():.3f}, blocks {sorted(set(frames))}")

    cache = pathlib.Path(args.cache) if args.cache else None
    if cache and cache.exists():
        z = np.load(cache)
        doc = {f: z[f] for f in FIELDS}
        qv = {k: z["q_" + k] for k in [*QUERIES, "ONE"]}
        print(f"embeddings loaded from {cache}")
    else:
        model = SentenceTransformer(args.model)
        doc = {}
        for f in FIELDS:
            texts = ["passage: " + (r[f] or "")[:1800] for r in rows]
            doc[f] = model.encode(texts, batch_size=32, normalize_embeddings=True,
                                  show_progress_bar=True)
            print(f"  encoded {f}: {doc[f].shape}")
        qv = {k: model.encode(["query: " + v], normalize_embeddings=True)[0]
              for k, v in QUERIES.items()}
        qv["ONE"] = model.encode(["query: " + QUERY_ONE], normalize_embeddings=True)[0]
        if cache:
            np.savez(cache, **doc, **{"q_" + k: v for k, v in qv.items()})
            print(f"embeddings cached to {cache}")

    hdr = (f"\n{'arm':26}{'sup':5}{'AUC':>7}{'AP':>7}{'prec':>8}{'recall':>7}{'F1':>7}"
           f"{'oracle':>8}   per-block F1 (leave-one-block-out threshold)")
    print("\n" + "=" * 110)
    print("GOAL 1 -- TEXT ONLY. Bar: rules_re:ta F1 0.475; floor (retrieve everything) 0.621.")
    print("=" * 110 + hdr)
    for f in FIELDS:
        rank_report(f"emb_one:{f}", frames, y, doc[f] @ qv["ONE"])
    for f in FIELDS:
        s = np.max(np.stack([doc[f] @ qv[k] for k in QUERIES]), axis=0)
        rank_report(f"emb_max6:{f}", frames, y, s)
    for f in FIELDS:
        s = np.mean(np.stack([doc[f] @ qv[k] for k in QUERIES]), axis=0)
        rank_report(f"emb_mean6:{f}", frames, y, s)

    print("\n-- supervised, NOT comparable with the keyword arms (they never saw a label) --")
    for f in FIELDS:
        s = np.zeros(len(y))
        for fr in sorted(set(frames)):
            te = frames == fr
            lr = LogisticRegression(max_iter=2000, C=1.0).fit(doc[f][~te], y[~te])
            s[te] = lr.predict_proba(doc[f][te])[:, 1]
        rank_report(f"emb_logreg:{f}", frames, y, s, tuned="sup")

    # ---- GOAL 2 -------------------------------------------------------------------------
    hit = np.array([r["impact"] in CLS_WORKS for r in rows])
    print("\n" + "=" * 110)
    print(f"GOAL 2 -- ON TOP OF METADATA. cls_works retrieves {int(hit.sum())} "
          f"({int(y[hit].sum())} noisy, {int((1 - y[hit]).sum())} quiet); "
          f"leaves {int((~hit).sum())} ({int(y[~hit].sum())} noisy, "
          f"{int((1 - y[~hit]).sum())} quiet).")
    print("=" * 110)
    best = max(FIELDS, key=lambda f: roc_auc_score(y, np.max(
        np.stack([doc[f] @ qv[k] for k in QUERIES]), axis=0)))
    s_best = np.max(np.stack([doc[best] @ qv[k] for k in QUERIES]), axis=0)
    print(f"using the strongest unsupervised text signal: emb_max6:{best}\n")
    for tag, mask in (("PRUNE  (inside cls_works's hits)", hit),
                      ("RECOVER(inside what it rejected)", ~hit)):
        yy, ss = y[mask], s_best[mask]
        if len(set(yy)) < 2:
            print(f"  {tag}: only one class present")
            continue
        k = int(yy.sum())
        topk = np.argsort(-ss)[:k]
        print(f"  {tag}: n={len(yy)} noisy={k}  AUC={roc_auc_score(yy, ss):.3f}  "
              f"AP={average_precision_score(yy, ss):.3f}  "
              f"P@{k}={yy[topk].mean():.3f}  (random would be {yy.mean():.3f})")

    print("\n-- end to end: does adding the embedding to the metadata features help? --")
    doct = sorted({r["doc_type"] for r in rows})
    acts = sorted({a for r in rows for a in (r["activiteit"] or [])})
    meta = np.array([[1.0 * (r["doc_type"] == d) for d in doct]
                     + [1.0 * (a in (r["activiteit"] or [])) for a in acts] for r in rows])
    print(f"   metadata features: {len(doct)} doc_type + {len(acts)} activiteit = {meta.shape[1]}")
    print(hdr)
    for name, X in (("meta_logreg", meta),
                    (f"meta+emb_logreg:{best}", np.hstack([meta, doc[best]]))):
        s = np.zeros(len(y))
        for fr in sorted(set(frames)):
            te = frames == fr
            lr = LogisticRegression(max_iter=3000, C=1.0).fit(X[~te], y[~te])
            s[te] = lr.predict_proba(X[te])[:, 1]
        rank_report(name, frames, y, s, tuned="sup")
    p, r, f = f1_at(y, hit.astype(float), 0.5)
    print(f"{'cls_works (hand-written)':26}{'dev':5}{'':7}{'':7}{p:8.3f}{r:7.3f}{f:7.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
