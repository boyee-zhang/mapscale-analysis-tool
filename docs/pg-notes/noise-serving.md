# Serving the noise classifier — and what measuring it at corpus scale changed

Companion to `noise-embedding.md`, which measured the model on 695 hand-labelled rows. This
one is about putting it behind `/bekendmakingen` and scoring all 61,853 publications. The
plumbing worked. **The headline claim did not survive the scale change**, and the useful part
of the model turned out to be a different part than the benchmark was measuring.

Run date 2026-11-04. Raw output: `raw/noise-serving-run1.txt`.

## 1. What shipped

- `migrations/0016_bekendmaking_noise_score.sql` — `noise_model` (one row per trained model,
  carrying the threshold and its measured metrics) + `bekendmaking_noise_score`
  (`identifier` → `score real`) + the `bekendmaking_noise_rank` view that applies the cutoff.
- `scripts/score_noise_embedding.py` — offline: embed, train, leave-one-block-out evaluate,
  score, upsert. Writes `models/noise_meta_emb_strict_v1.json` (coefficients, vocabulary,
  threshold, metrics) so a re-score needs no retrain and no GPU.
- `/bekendmakingen` gains `noise` (`noisy` / `quiet` / `unscored`) and `noise_score` per item,
  a `by_noise` facet, a `noise=` filter, a `sort=noise` option and `noise_models` provenance.
- `PermitLayer` colours by score.

**Why the score is precomputed.** A request cannot embed the documents it matches: a 10-minute
walk over a year returns 3,291 publications, and a vector is a property of the document,
unchanged since the import. So document vectors are computed once and the request path joins
one `real`. Measured cost of that join: no change in the shape of the plan.

> **Correction, after the first version of this note.** I originally gave the reason as
> Vercel's function size limit — "torch is 1.1 GB and this deploys as one Mangum-wrapped
> function". That is wrong. `vercel.json` and `mangum` are still in the repo, but they are
> legacy: this project runs its own Postgres and its own containers
> (`docker-compose.deploy.yml`), so nothing stops torch living beside the API. The corpus
> argument above is the real one, and unlike the Vercel one it does not depend on the host.
> **Nothing in sections 2–5 changes** — those are measurements of what the model knows, not of
> where it runs. What changes is section 7.

Embeddings are **not yet** stored. 768 float4 × 61,853 is ~190 MB against ~1.5 MB for the
scores, in a container running `shared_buffers=128MB` on purpose, and no current query reads
them — the request path needs the score, and a retrain re-embeds from `activity_text`, which is
already stored. The npz cache lives in the gitignored `data/`. This is a "not yet": see §7.

## 2. The threshold, and a rule of mine that was invalid

The benchmark reported **F1 0.891**. What ships measures **0.875**. Both are correct and the
difference is the thing to carry forward: the benchmark scored each block at *its own fold's*
threshold, which is right for comparing methods and is **not available in production** — a
live request has no "block" to borrow a cutoff from. One shared threshold costs 0.016 F1.
AUC and AP are identical (0.950 / 0.920), so it is the cutoff that differs, not the model.

I then tried to buy precision, on the reasoning that a red badge names a specific address so a
false positive costs more than a miss. **The first implementation was invalid and produced a
number that looked fine.** "Lowest cutoff whose precision clears 0.90" applied to the
*training fold's own* predicted probabilities returned t=0.4748 at an actual held-out precision
of **0.825** — because an in-sample logistic fit on 786 features and ~460 rows is nearly
separable, so its precision clears any floor almost immediately. **In-sample precision is not a
forecast of held-out precision, and a threshold rule that reads one as the other silently does
nothing.** Fixed with nested selection: the cutoff for each outer fold now comes from inner
out-of-fold scores over the other two blocks.

With that fixed, the trade is real and was a decision, not a default:

| basis | t | precision | recall | F1 |
|---|---|---|---|---|
| argmax F1 (shipped) | 0.383 | 0.821 | 0.936 | **0.875** |
| precision floor 0.90 | 0.775 | **0.927** | 0.725 | 0.814 |

Shipped the argmax, because 0.814 is **below the 0.845 the hand-written rule table in 0012
already scores** — buying precision there means shipping a classifier worse than the one we
have. `--precision-floor` stays in the script, so revisiting it is one flag and a rescore, and
the threshold lives in a `noise_model` row so moving it is a one-field UPDATE, not a
61,853-row backfill.

## 3. The finding that changes what this is: the binary verdict is a doc_type lookup

Corpus-wide the flag rate is 36.9%, but per `doc_type` it is nearly deterministic, and nearly
identical in both cities:

| doc_type | Amsterdam | flagged | Utrecht | flagged |
|---|---|---|---|---|
| omgevingsvergunning | 12,882 | 100% | 4,671 | 100% |
| evenementenvergunning | 1,592 | 100% | 497 | 100% |
| omgevingsmelding | 1,014 | 100% | 295 | 100% |
| andere beschikking | 1,250 | 99% | 17 | 100% |
| andere vergunning | 31,852 | 1% | 443 | 0% |
| verkeersbesluit | 2,155 | 0% | 1,226 | 0% |
| exploitatievergunning | 2,634 | 0% | 73 | 0% |

So:

```sql
doc_type IN ('omgevingsvergunning','evenementenvergunning',
             'omgevingsmelding','andere beschikking')
```

- **agrees with the 768-d model on 99.61% of the corpus** (61,611 of 61,853)
- scores **precision 0.821 / recall 0.936 / F1 0.875** on the 695 labelled rows — *identical
  to three decimals* to the model's out-of-fold result.

**As a binary verdict, the embedding buys nothing.** 1.1 GB of dependency, a 190 MB cache and
a 768-d vector reproduce a four-value predicate. A red/grey dot on the map would have dressed
a permit-type lookup up as a prediction, which is why the map now shows a gradient instead.

### Two alarms this raised, one false and one real

**False: "Utrecht is 75% noisy against Amsterdam's 31.5%, the model is city-biased."** Wrong —
the table above shows the per-type rates are the same in both. The 2.4x gap is pure
composition: Amsterdam is 59% `andere vergunning` (flagged ~1%), Utrecht is 64%
`omgevingsvergunning` (flagged 100%). Recorded because the aggregate genuinely looks like bias
and is not.

**Real: 17,804 of 17,813 omgevingsvergunningen are flagged (99.95%)**, including hand-labelled
*quiet* ones, confidently — 34 of the 36 quiet ones in the frames:

| score | activity |
|---|---|
| 0.907 | `het plaatsen van een muurschildering` (painting a mural) |
| 0.873 | `het legaliseren van een dakterras` (legalising an existing terrace — paperwork) |
| 0.869 | `verlengen van de instandhoudingstermijn` (extending a permit's validity) |
| 0.869 | `wijzigen van gevelkleur` (changing a facade colour) |

## 4. Why that is a ground-truth problem, not a model bug — and why re-cutting it fails

Inside `activiteit = 'bouwen'` (22.8% of the corpus, 14,130 rows), the labels are:

| label | n |
|---|---|
| noisy | 152 |
| **borderline** | **157** |
| quiet | 16 |
| unjudgeable | 7 |

The strict universe trains on `noisy` + `quiet` only, so it sees **152 against 16** — a 90.5%
base rate — and the 157 `borderline` rows, which are exactly the mural / roof-terrace /
permit-extension cases where the distinction lives, are **excluded by construction**. The model
learns "bouwen implies noisy" because that is precisely what its training data says. The
`strict` universe is the right one for *comparing methods* and the wrong one for *training a
classifier*: it deletes the hard cases that dominate the corpus.

The obvious fix does not work. Retraining with those rows as negatives (`wide`) collapses:

| universe | train n | base rate | AUC | AP | precision | recall | F1 | corpus flagged |
|---|---|---|---|---|---|---|---|---|
| strict (shipped) | 695 | 0.450 | 0.950 | 0.920 | 0.821 | 0.936 | **0.875** | 36.9% |
| wide | 1,309 | 0.239 | 0.895 | 0.733 | **0.505** | 0.930 | 0.655 | 35.1% |
| lenient | 1,309 | 0.708 | 0.935 | 0.955 | 0.827 | 0.991 | 0.902 | **92.1%** |

`wide` drops precision to 0.505 and AP to 0.733: **the noisy-vs-borderline boundary is not
learnable from this text and metadata at this amount of labelled data.** `lenient` flags 92.1%
of the corpus, which is not a classifier. So the saturation cannot be fixed by re-cutting the
labels, and the honest reading is that `borderline` was an accurate description of those rows.

Supporting evidence that the model is well-calibrated about its own uncertainty: over the 614
`borderline` rows its mean score is **0.490**, sitting on the threshold. It is genuinely unsure
exactly where humans were unsure.

## 5. What the embedding *did* earn: ranking inside a type

Within one `doc_type`, the lookup is constant and carries no information whatsoever. The score
does:

| doc_type | noisy | quiet | within-class AUC |
|---|---|---|---|
| omgevingsvergunning | 202 | 36 | **0.911** |
| evenementenvergunning | 66 | 24 | **0.965** |
| andere vergunning | 20 | 82 | **1.000** |

This is the part worth shipping, and it is a **ranking, not a verdict**. Hence `sort=noise` on
the endpoint and a colour ramp on the map. `sort` is not a convenience: `items` is capped at
500, so a date order fills the cap with the newest publications and "what is loudest near here"
cannot be answered at all without it.

## 6. Scale of validity

Trained on 695 rows in three frozen 2026 blocks (Amsterdam ×2, Utrecht ×1); applied to 61,853
rows. 99.9% of the corpus is Amsterdam, Utrecht or Diemen, so the geography is largely in
scope, but **Diemen's 848 rows were never labelled** (47.4% flagged) and no other municipality
has more than one row. `noise_model.metrics` carries the figures so a consumer can weigh a
badge instead of trusting it.

Two narrower caveats:
- **The 695 labelled rows are scored by a model trained on them**, so their stored scores are
  in-sample (P 0.831 / R 0.971 / F1 0.895 against the hand labels, versus 0.875 out of fold).
  Those specific publications look better classified than the rest of the corpus.
- **The one-hot vocabulary comes from the labelled rows, not the corpus.** 77 rows (0.12%)
  carry an unseen `doc_type` and are scored from the embedding and `activiteit` alone. One of
  those is conceptually interesting: 0012 matches `doc_type ~* 'evenement'`, and **a regex
  reaches inside a value** — it catches `evenementmelding`, which a one-hot column cannot,
  because an unseen category is all-zeros. Unseen-category generalisation is something the
  substring rule has and the encoding does not. 2 rows, so it is written down, not patched.

## 7. Open

- **The recommendation this measurement implies has not been acted on.** If the binary verdict
  is wanted, `doc_type IN (4 values)` is measurably as good, runs in SQL, needs no offline
  batch, and scores new rows the moment they are imported. The embedding should be kept for
  the ranking. Shipped as-is pending that call, because the ranking is real and the plumbing is
  the same either way.
- **pgvector is available and is the obvious next step, now that the Vercel premise is gone.**
  Storing `vector(768)` beside the score opens the thing this whole line of work was heading
  toward: nearest-neighbour search over publications, where the query is free text a resident
  types rather than one of my six hand-written clauses. That also removes the weakest part of
  `noise-embedding.md` — that the six query clauses are mine — because the user supplies the
  query instead. Two things make it worth doing here specifically: embedding a *query* at
  request time is one short forward pass, not 3,291; and 190 MB of vectors against a 128 MB
  buffer pool is exactly the regime where an HNSW index's behaviour is measurable rather than
  hidden by cache.
- **Stage 2 of `docs/postgres-refactor-plan.md` is also unblocked by the same correction.** It
  was recorded as needing a long-lived process and therefore blocked on the backend leaving
  Vercel. It has.
- `unjudgeable`: 7 of 7 flagged, mean score 0.860. The model has no abstain mechanism and is
  confident on rows whose record does not say enough to judge.
- Only one embedding model has ever been tried (`multilingual-e5-base`). A second would tell
  us whether 0.950 AUC is the model or the task.
- `bekendmaking_fts_tmp` (used by every FTS arm in `noise-retrieval-fts.md`) is still in no
  migration, so that benchmark is not reproducible on a fresh database. Unrelated to this
  change; still open.
- `ts_rank_cd` does no document-frequency weighting. Does not affect any boolean P/R/F1 here,
  but must be fixed before any ranked keyword comparison.
