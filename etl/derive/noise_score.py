"""
Score every publication with the measured noise classifier, offline.

WHY THIS IS A SCRIPT AND NOT A REQUEST PATH. The design this implements is the one measured in
docs/pg-notes/noise-embedding.md: logistic regression over 18 metadata features concatenated
with a 768-d `intfloat/multilingual-e5-base` embedding of `bekendmaking_activity.activity_text`
(cross-validated F1 0.891, against 0.845 for the hand-written rule table in migration 0012 and
0.621 for retrieving everything). It runs here rather than in the request path because a
request cannot re-embed the documents it matches: a 10-minute walk over a year returns ~3,300
publications, and their vectors are a property of the document, unchanged since the import. So
they are computed once and the request path joins one `real`.

(An earlier version of this docstring said the reason was Vercel's function size limit. That
was wrong -- this project runs its own Postgres and its own containers. The corpus argument
above is the real one and does not depend on the host. Embedding a QUERY at request time is
cheap and is a separate question; see the pgvector note in migration 0016.)

    python3 -m venv /tmp/embvenv
    /tmp/embvenv/bin/pip install sentence-transformers==3.4.1 scikit-learn==1.6.1 \
        asyncpg python-dotenv
    /tmp/embvenv/bin/python -m etl.derive.noise_score --cache data/noise_emb_cache.npz

Re-running is idempotent: it upserts, and with `--cache` present it skips the embedding pass.
A later `import_bekendmaking.py` run adds unscored rows; they surface through
`bekendmaking_noise_rank` as `is_noisy = NULL`, never as "quiet", and this script scores them on
its next run. `--only-new` does that without re-embedding the whole corpus.

THE SCALE OF VALIDITY, stated here because it is part of the number. The model is trained on 695
hand-labelled rows (strict universe: `noisy` vs `quiet`, `borderline` and `unjudgeable`
excluded) in three frozen geographic blocks -- two in Amsterdam, one in Utrecht -- all published
in 2026. It is applied to all 61,853 rows in the table. 99.9% of those are Amsterdam, Utrecht or
Diemen, so the geography is largely in scope, but Diemen (848 rows) was never labelled and no
other municipality has more than one row. The metrics written to `noise_model.metrics` are the
leave-one-block-out figures; they are not a claim about a fourth city.

THE ONE-HOT VOCABULARY COMES FROM THE LABELLED ROWS, NOT THE CORPUS. `doc_type` has 15 distinct
values corpus-wide and the frame saw 10; `activiteit` has 9 and the frame saw 8. Building the
columns from the corpus instead would add six columns that are constant-zero in training, which
logistic regression gives a zero coefficient anyway -- mathematically the same model with six
dead columns. So the frame's vocabulary is used, and the 77 rows (0.12%) carrying an unseen
`doc_type` are scored from their embedding and their `activiteit` block alone.

That exposes one real regression against the rule table, recorded rather than patched: 0012
matches `doc_type ~* 'evenement'`, and a REGEX REACHES INSIDE A VALUE -- it catches
`evenementmelding`, a type the frame never saw. A one-hot column cannot: an unseen category is
all-zeros, so the learned model has no doc_type signal for those 2 rows. Unseen-category
generalisation is something the substring rule has and the encoding does not. 2 rows of 61,853,
so it is written down and left; the fix, if it is ever worth it, is a coarser `doc_type` family
feature rather than a 15th column.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
from datetime import datetime, timezone

import asyncpg
import numpy as np
from ..core import db
from sentence_transformers import SentenceTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

# Read .env directly rather than through api.main.db, which is how the other scripts here get
# the DSN. This one cannot: it runs in a throwaway venv holding torch and sentence-transformers
# (~1.1 GB) and NOT the application's dependencies, and importing api.main.db pulls in
# api.main.logger, which pulls in starlette -- the whole web stack, to read one string.
#
# The separate venv is a tidiness choice, not a constraint: nothing stops torch being installed
# beside the API here. Keep them apart while only this batch job needs it; merge them the day
# the API itself embeds queries.
ROOT = pathlib.Path(__file__).resolve().parents[2]
# core.db: an exported DATABASE_URL now wins over .env, which `dotenv_values` used to
# ignore. required=False because this is a module-level constant checked later.
DATABASE_URL = db.dsn(required=False)

MODEL = "intfloat/multilingual-e5-base"
VERSION = "meta_emb_v1"
# The same truncation the benchmark measured at. `activity_text` averages far less than this;
# the cap only binds on the few publications that concatenate six source fields.
MAX_CHARS = 1800

CORPUS_SQL = """
SELECT a.identifier, b.doc_type, b.activiteit, a.activity_text
FROM bekendmaking_activity a JOIN bekendmaking b USING (identifier)
ORDER BY a.identifier
"""

# The training set: the frozen frames, strict universe only. `noise_frame_member` defines
# membership by geography and time and never by the concept, which is what makes recall
# measurable at all -- see docs/pg-notes/noise-groundtruth.md.
TRAIN_SQL = """
SELECT m.frame, l.label, b.identifier, b.doc_type, b.activiteit, a.activity_text
FROM noise_frame_member m
JOIN bekendmaking b USING (identifier)
JOIN bekendmaking_activity a USING (identifier)
JOIN noise_label l ON l.identifier = b.identifier
WHERE l.label <> 'unjudgeable'
ORDER BY b.identifier
"""

# Which labels count as a positive, and which rows are trained on at all.
#
# This choice is NOT cosmetic and it is the one that decides what ships. `strict` is the
# universe every benchmark in docs/pg-notes/ reports, because dropping `borderline` removes the
# one subjective cut and makes methods comparable. It is the WRONG universe to train a
# production classifier on, and the measurement that showed why is in docs/pg-notes/
# noise-serving.md: inside `activiteit = bouwen` the strict universe holds 152 noisy against 16
# quiet, while 157 `bouwen` rows are labelled `borderline` and are therefore excluded outright.
# So the model correctly learns "bouwen implies noisy" -- and flags 17,804 of 17,813
# omgevingsvergunningen, including hand-labelled quiet ones like `het plaatsen van een
# muurschildering` (0.907) and `verlengen van de instandhoudingstermijn` (0.869). A classifier
# that red-flags every building permit has reduced itself to `building_permits=true`, which the
# endpoint already answers without a model.
#
# `wide` puts those 157 rows back as NEGATIVES, which is what a badge should mean: `borderline`
# was defined as "a reasonable resident could go either way", and that does not deserve a red
# dot. `lenient` calls them positives and is kept only to show the direction.
UNIVERSES = {
    "strict": {"positive": ("noisy",), "train_on": ("noisy", "quiet")},
    "wide": {"positive": ("noisy",), "train_on": ("noisy", "borderline", "quiet")},
    "lenient": {"positive": ("noisy", "borderline"),
                "train_on": ("noisy", "borderline", "quiet")},
}


def features(rows, doct, acts, emb):
    """18 metadata columns then 768 embedding columns, in that fixed order.

    `doc_type` is an unordered categorical, so it is one-hot: integer-encoding it would assert
    a magnitude ordering between permit types that logistic regression would read as real.
    `activiteit` is a closed-vocabulary ARRAY and a publication can carry several at once, so
    it is multi-hot -- not one-hot, and the distinction is load-bearing: 284 central-Amsterdam
    `bouwen` permits also carry `ruimtelijke ordening`, and a one-of-N encoding would have to
    discard one of the two.
    """
    meta = np.array(
        [
            [1.0 * (r["doc_type"] == d) for d in doct]
            + [1.0 * (a in (r["activiteit"] or [])) for a in acts]
            for r in rows
        ],
        dtype=np.float32,
    )
    return np.hstack([meta, emb])


def encode(model, rows):
    texts = ["passage: " + (r["activity_text"] or "")[:MAX_CHARS] for r in rows]
    return model.encode(
        texts, batch_size=32, normalize_embeddings=True, show_progress_bar=True
    ).astype(np.float32)


def f1_at(y, s, t):
    pred = s >= t
    tp = int((pred & (y == 1)).sum())
    if tp == 0:
        return 0.0, 0.0, 0.0
    p, r = tp / int(pred.sum()), tp / int((y == 1).sum())
    return p, r, 2 * p * r / (p + r)


def best_threshold(y, s, precision_floor=None):
    """A cutoff chosen on the rows given. Only ever called on TRAINING folds.

    With `precision_floor`, the rule is "the lowest cutoff whose precision clears the floor",
    i.e. buy precision up to the bar and spend everything left on recall. Without it, the plain
    F1 argmax.

    Why the floor is applied here, per fold, and not by reading the pooled out-of-fold curve:
    the pooled curve is the evaluation, and picking a number off it because it looks good is
    selection on the test set -- the same contamination the folds exist to prevent. Measured on
    this data the two routes land in the same place, which is reassuring and is not a reason to
    take the cheap one.
    """
    cand = np.unique(np.round(s, 4))
    if precision_floor is None:
        return max(cand, key=lambda t: f1_at(y, s, t)[2])
    ok = [t for t in cand if f1_at(y, s, t)[0] >= precision_floor]
    # No cutoff reaches the floor on this fold: fall back to the most precise one available
    # rather than silently returning an F1-optimal cutoff that ignores the instruction.
    return min(ok) if ok else max(cand, key=lambda t: f1_at(y, s, t)[0])


async def load(sql):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return [dict(r) for r in await conn.fetch(sql)]
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--version", default=None,
                    help="noise_model.version; defaults to meta_emb_<universe>_v1")
    ap.add_argument("--cache", default=None, help="npz to cache corpus embeddings in")
    ap.add_argument("--only-new", action="store_true",
                    help="score only publications with no row yet; requires --cache")
    ap.add_argument("--dry-run", action="store_true", help="train and report, write nothing")
    ap.add_argument("--universe", default="strict", choices=sorted(UNIVERSES),
                    help="which labels train and which count as noisy; see UNIVERSES")
    ap.add_argument("--sweep", action="store_true",
                    help="print the out-of-fold precision/recall curve and exit")
    ap.add_argument("--threshold", type=float, default=None,
                    help="use this cutoff instead of the leave-one-block-out argmax")
    ap.add_argument("--precision-floor", type=float, default=None,
                    help="per-fold rule: lowest cutoff whose TRAINING-fold precision clears "
                         "this. A red badge names a specific address, so a false positive "
                         "costs more than a miss; 0.90 is the product call, not a measurement")
    args = ap.parse_args()

    args.version = args.version or f"meta_emb_{args.universe}_v1"
    if not DATABASE_URL:
        print("DATABASE_URL is not set in .env")
        return 1

    uni = UNIVERSES[args.universe]
    train = [r for r in asyncio.run(load(TRAIN_SQL)) if r["label"] in uni["train_on"]]
    corpus = asyncio.run(load(CORPUS_SQL))
    frames = np.array([r["frame"] for r in train])
    y = np.array([1 if r["label"] in uni["positive"] else 0 for r in train])
    print(f"universe {args.universe}: positives {uni['positive']}, trained on "
          f"{uni['train_on']}")
    print(f"train: {len(train)} rows, {int(y.sum())} noisy, base rate {y.mean():.3f}, "
          f"blocks {sorted(set(frames))}")
    print(f"corpus: {len(corpus)} rows")

    doct = sorted({r["doc_type"] for r in train})
    acts = sorted({a for r in train for a in (r["activiteit"] or [])})
    print(f"features: {len(doct)} doc_type one-hot + {len(acts)} activiteit multi-hot "
          f"= {len(doct) + len(acts)}, + 768 embedding")
    unseen = sum(1 for r in corpus if r["doc_type"] not in doct)
    print(f"corpus rows with an unseen doc_type (scored without that block): {unseen}")

    model = None
    cache = pathlib.Path(args.cache) if args.cache else None
    if cache and cache.exists():
        z = np.load(cache, allow_pickle=True)
        ids = list(z["ids"])
        by_id = dict(zip(ids, z["emb"]))
        missing = [r for r in corpus if r["identifier"] not in by_id]
        print(f"embeddings loaded from {cache}: {len(ids)} cached, {len(missing)} missing")
        if missing:
            model = SentenceTransformer(args.model)
            new = encode(model, missing)
            for r, v in zip(missing, new):
                by_id[r["identifier"]] = v
            np.savez(cache, ids=np.array([r["identifier"] for r in corpus]),
                     emb=np.stack([by_id[r["identifier"]] for r in corpus]))
            print(f"cache extended to {len(corpus)}")
        c_emb = np.stack([by_id[r["identifier"]] for r in corpus])
    else:
        if args.only_new:
            print("--only-new needs an existing --cache; nothing to extend")
            return 1
        model = SentenceTransformer(args.model)
        print(f"encoding {len(corpus)} publications")
        c_emb = encode(model, corpus)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez(cache, ids=np.array([r["identifier"] for r in corpus]), emb=c_emb)
            print(f"embeddings cached to {cache}")

    # The training rows are a subset of the corpus, so their embeddings come from the same
    # pass. Re-encoding them separately would be a second, silently different input.
    idx = {r["identifier"]: i for i, r in enumerate(corpus)}
    t_emb = np.stack([c_emb[idx[r["identifier"]]] for r in train])

    X = features(train, doct, acts, t_emb)

    # Leave one block out: the threshold for each block comes from the OTHER blocks, never from
    # the block being scored. A cutoff tuned on the rows it is then measured on is not a
    # measurement, and this is the one place a score-based method can quietly cheat.
    # NESTED selection. The cutoff for each outer fold is chosen on INNER out-of-fold scores
    # -- the other blocks scored by models that did not see them -- never on the outer fold,
    # and never on in-sample fits.
    #
    # The inner loop is not ceremony. The first version of this picked the cutoff from the
    # training fold's own predicted probabilities, and the precision rule collapsed: an
    # in-sample logistic fit on 786 features and ~460 rows is nearly separable, so its
    # precision clears any floor at a very low threshold and "lowest t with precision >= 0.90"
    # returned 0.47 at an actual held-out precision of 0.825. In-sample precision is not a
    # forecast of held-out precision, and a threshold rule that reads one as the other silently
    # does nothing. Measured, not reasoned about: that run is in docs/pg-notes/noise-serving.md.
    oof = np.zeros(len(y))
    per_block, thresholds = {}, []
    for fr in sorted(set(frames)):
        te = frames == fr
        lr = LogisticRegression(max_iter=3000, C=1.0).fit(X[~te], y[~te])
        oof[te] = lr.predict_proba(X[te])[:, 1]

        inner_frames = sorted(set(frames[~te]))
        inner = np.zeros(int((~te).sum()))
        Xi, yi, fi = X[~te], y[~te], frames[~te]
        for ifr in inner_frames:
            ite = fi == ifr
            ilr = LogisticRegression(max_iter=3000, C=1.0).fit(Xi[~ite], yi[~ite])
            inner[ite] = ilr.predict_proba(Xi[ite])[:, 1]
        t = best_threshold(yi, inner, args.precision_floor)

        thresholds.append(float(t))
        per_block[fr] = round(f1_at(y[te], oof[te], t)[2], 3)

    # The median of the per-fold argmax, NOT the pooled leave-one-block-out F1 the benchmark
    # reported. That difference is worth being explicit about: the benchmark scored each block
    # at its own fold's threshold, which is correct for comparing methods but is not available
    # in production -- a live request has no "block" to borrow a cutoff from. One threshold for
    # everything is the honest production measurement, and it costs about 0.014 F1 against the
    # 0.891 headline (identical AUC/AP, so it is the cutoff that differs, not the model).
    threshold = args.threshold if args.threshold is not None else float(np.median(thresholds))

    if args.sweep:
        print(f"\nout-of-fold threshold sweep (n={len(y)}, {int(y.sum())} noisy)")
        print(f"{'t':>6}{'prec':>8}{'recall':>8}{'F1':>8}{'flagged':>9}")
        for t in np.arange(0.30, 0.86, 0.05):
            pp, rr, ff = f1_at(y, oof, float(t))
            print(f"{t:6.2f}{pp:8.3f}{rr:8.3f}{ff:8.3f}{int((oof >= t).sum()):9d}")
        return 0

    p, r, f1 = f1_at(y, oof, threshold)
    auc, apr = roc_auc_score(y, oof), average_precision_score(y, oof)
    print(f"\nout-of-fold  AUC {auc:.3f}  AP {apr:.3f}")
    print(f"threshold {threshold:.4f} (median of per-fold inner-CV choices {thresholds})")
    print(f"  precision {p:.3f}  recall {r:.3f}  F1 {f1:.3f}")
    print(f"  per-block F1 {per_block}")

    basis = ("manual" if args.threshold is not None
             else f"loblo_median_precision_floor_{args.precision_floor}"
             if args.precision_floor is not None else "loblo_median_argmax_f1")
    metrics = {
        "universe": args.universe, "threshold_basis": basis, "roc_auc": round(auc, 4), "average_precision": round(apr, 4),
        "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
        "per_block_f1": per_block, "fold_thresholds": [round(t, 4) for t in thresholds],
        "n_noisy": int(y.sum()), "base_rate": round(float(y.mean()), 4),
        "baseline_rule_table_f1": 0.845, "baseline_retrieve_all_f1": 0.621,
        "trained_on": "3 frozen blocks, Amsterdam x2 + Utrecht, published 2026",
    }

    # The final model is fit on ALL labelled rows -- more data is a better model -- while the
    # metrics above come from folds that never saw the rows they scored. Reporting the
    # in-sample fit of this final model would be the cheat the folds exist to prevent.
    final = LogisticRegression(max_iter=3000, C=1.0).fit(X, y)
    scores = final.predict_proba(features(corpus, doct, acts, c_emb))[:, 1]
    flagged = int((scores >= threshold).sum())
    print(f"\nscored {len(corpus)} publications; {flagged} ({flagged / len(corpus):.1%}) "
          f"at or above the threshold")

    model_json = ROOT / "models" / f"noise_{args.version}.json"
    if not args.dry_run:
        model_json.parent.mkdir(parents=True, exist_ok=True)
        model_json.write_text(json.dumps({
            "version": args.version, "embed_model": args.model, "threshold": threshold,
            "max_chars": MAX_CHARS, "text_prefix": "passage: ",
            "text_field": "bekendmaking_activity.activity_text",
            "doc_type_onehot": doct, "activiteit_multihot": acts,
            "intercept": float(final.intercept_[0]),
            "coef": [round(float(c), 6) for c in final.coef_[0]],
            "metrics": metrics,
        }, indent=2, ensure_ascii=False) + "\n")
        print(f"model written to {model_json}")
        asyncio.run(write(args, threshold, metrics, len(train), corpus, scores))
    else:
        print("--dry-run: nothing written")
    return 0


async def write(args, threshold, metrics, n_train, corpus, scores):
    now = datetime.now(timezone.utc)
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO noise_model (version, trained_at, embed_model, threshold,
                                         threshold_basis, n_train, metrics, note)
                VALUES ($1, $2, $3, $4, $8, $5, $6::jsonb, $7)
                ON CONFLICT (version) DO UPDATE SET
                    trained_at = EXCLUDED.trained_at, embed_model = EXCLUDED.embed_model,
                    threshold = EXCLUDED.threshold, n_train = EXCLUDED.n_train,
                    metrics = EXCLUDED.metrics, note = EXCLUDED.note
                """,
                args.version, now, args.model, threshold, n_train, json.dumps(metrics),
                "18 metadata features (10 doc_type one-hot + 8 activiteit multi-hot) "
                "+ 768-d e5 embedding of activity_text, logistic regression. "
                "Metrics are leave-one-block-out; see etl/derive/noise_score.py.",
                metrics["threshold_basis"],
            )
            # One statement, not 61,853 round trips. asyncpg's copy path needs the rows as
            # tuples; the upsert goes through a temp table because COPY cannot ON CONFLICT.
            await conn.execute(
                "CREATE TEMP TABLE _score (identifier text, model_version text, "
                "score real, scored_at timestamptz) ON COMMIT DROP"
            )
            await conn.copy_records_to_table(
                "_score",
                records=[(r["identifier"], args.version, float(s), now)
                         for r, s in zip(corpus, scores)],
            )
            res = await conn.execute(
                """
                INSERT INTO bekendmaking_noise_score
                    (identifier, model_version, score, scored_at)
                SELECT identifier, model_version, score, scored_at FROM _score
                ON CONFLICT (identifier) DO UPDATE SET
                    model_version = EXCLUDED.model_version, score = EXCLUDED.score,
                    scored_at = EXCLUDED.scored_at
                WHERE bekendmaking_noise_score.score IS DISTINCT FROM EXCLUDED.score
                   OR bekendmaking_noise_score.model_version
                      IS DISTINCT FROM EXCLUDED.model_version
                """
            )
            print(f"upsert: {res}")
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
