# Does an embedding beat keyword search here, and does it add to the metadata classifier?

Run 1, 2026-11-03. Harness `scripts/experiments/noise_embedding_bench.py`, raw output in
`raw/noise-embedding-run1.txt`. Model `intfloat/multilingual-e5-base`, 768-d, run locally on
CPU. Answer key: the 1,316-row hand-labelled ground truth; strict universe = 695 rows over
three blocks, 313 noisy, base rate 0.450.

**The two questions are answered apart on purpose.** The run-1 FTS notes put `cls_works`
(F1 0.845) in one table beside the FTS arms and invited the wrong comparison — 87% of that
arm's true positives come from two *structured* fields, not from matching text. An embedding is
a text method, so the comparison that means something is against the text arms, and the
separate question is whether it adds anything on top of the metadata.

| | bar to beat |
|---|---|
| **Goal 1** text vs text | best text arm `rules_re:ta` **F1 0.475**; ranking **AUC 0.651 / AP 0.666**; floor (retrieve everything) **F1 0.621**, which no text method had ever cleared |
| **Goal 2** on top of metadata | `cls_works` **F1 0.845**, and a learned metadata model at **0.864** |

---

## Goal 1: yes, decisively — and the first text method to clear the floor

Unsupervised, because the keyword arms it is compared against never saw a label: a human writes
a description of what they want, the engine ranks. The query text is the `noisy` test from
`noise-groundtruth.md` translated into Dutch — the same information the 0012 vocabulary census
had, and strictly less than the labels.

| arm | AUC | AP | prec | recall | F1 | per-block F1 |
|---|---|---|---|---|---|---|
| keyword: 0012 patterns matched | 0.651 | 0.666 | — | — | 0.475 | 0.250 – 0.706 |
| `emb_one:ta` (one sentence) | 0.689 | 0.662 | 0.536 | 0.847 | 0.657 | 0.575 – 0.757 |
| **`emb_max6:ta`** (six clauses, max) | **0.862** | 0.850 | 0.667 | 0.904 | **0.768** | 0.733 – 0.793 |
| `emb_max6:act` | 0.866 | **0.873** | 0.761 | 0.722 | 0.741 | 0.638 – 0.811 |
| `emb_mean6:ta` (six clauses, mean) | 0.773 | 0.788 | 0.740 | 0.610 | 0.669 | 0.600 – 0.733 |

Ranking quality roughly **doubles the keyword baseline's lift** — AUC 0.651 → 0.862, AP 0.666 →
0.850 — and F1 goes 0.475 → 0.768, past the 0.621 floor. Recall is where it comes from: 0.319
→ 0.904. That is exactly the deficit the FTS run diagnosed and could not fix, because
`funderingsherstel doormiddel van schroefinjectie` is loud for reasons no word in it carries.

### The gain is not "embeddings are magic". It is the decomposition.

**One sentence describing the concept scores AUC 0.689 — barely above the keyword baseline's
0.651.** The same model, same documents, same field, with the concept split into its six clauses
and max-pooled, scores 0.862. Almost the whole gain is in that step.

And **max beats mean by a wide margin** (0.862 vs 0.773). The concept is a *disjunction* —
structure OR groundwork OR demolition OR tree felling OR rebuild OR amplified sound — and max
is the disjunction operator over clause similarities, where mean dilutes every clause with the
five that do not apply. A boolean OR query has the right shape for this concept; a single
embedded sentence does not. The lesson transfers: **decompose a disjunctive concept into
clauses and max-pool, rather than writing one sentence and hoping the vector space averages
well.**

### Two caveats that the headline must carry

**The F1 comparison is not apples-to-apples; the AUC comparison is.** A cosine score needs a
cutoff to become a set. The cutoff here comes from the *other two blocks*
(leave-one-block-out) — never from the block being scored — but that is still a threshold
derived from labels, which no keyword arm had. The threshold-free metrics are the clean
comparison, and the embedding wins those too (AUC 0.862 vs 0.651), so the conclusion holds
either way. The ranking is also not sensitive to the cutoff: the leave-one-block-out F1 (0.768)
is within 0.006 of the oracle best-threshold F1 (0.774), so the threshold transfers between
blocks.

**It is also the most stable arm measured in this project.** Per-block F1 spreads: `emb_max6:ta`
0.733–0.793 (1.08x), against `cls_works` 1.12x, `rules_re:ta` 2.8x, and `naive_re:ta`
unbounded.

### Supervised, listed apart because it is not the same question

| arm | AUC | F1 |
|---|---|---|
| `emb_logreg:ta` | 0.935 | 0.837 |
| `emb_logreg:body` | 0.857 | 0.843 |

A logistic regression on the text embedding alone, trained on two blocks and tested on the
third, scores **0.837–0.843 — level with the hand-written metadata classifier's 0.845**, from
text only. That is a different claim from goal 1 and is not evidence about query-time
retrieval; it is evidence that the concept is learnable from a few hundred examples.

---

## Goal 2: yes, but modestly — and the value is in recall, not precision

`cls_works` retrieves 293 of 695 (256 noisy, 37 quiet) and leaves 402 behind (57 noisy, 345
quiet). So "does text add anything" is two ranking tasks with a known answer key.

| task | n | noisy | AUC | P@k | random baseline |
|---|---|---|---|---|---|
| **PRUNE** — inside what it retrieved | 293 | 256 | 0.848 | 0.941 | 0.874 |
| **RECOVER** — inside what it rejected | 402 | 57 | **0.903** | **0.684** | **0.142** |

RECOVER is where the text earns its place: taking the top 57 by embedding score out of the 402
documents the metadata rejected finds 39 of the 57 it missed — **4.8x better than chance**.
PRUNE helps much less: it lifts precision inside the retrieved set from 0.874 to 0.941, useful
but a smaller move on a set that was already 87% correct.

End to end, leave-one-block-out:

| model | features | AUC | prec | recall | F1 |
|---|---|---|---|---|---|
| `cls_works` (hand-written rules) | metadata | — | 0.874 | 0.818 | 0.845 |
| `meta_logreg` | 18 metadata one-hots | 0.928 | 0.819 | 0.914 | 0.864 |
| **`meta+emb_logreg:act`** | 18 + 768 | **0.950** | **0.919** | 0.866 | **0.891** |

Adding the embedding to the metadata features is worth **+0.027 F1** over a learned metadata
model and **+0.046** over the hand-written rules, almost all of it precision (0.819 → 0.919) at
a small recall cost. Real, reproducible, and not a transformation of the problem.

Worth noting on the way past: **18 one-hot features over `doc_type` and `activiteit` score
0.864 by themselves**, beating the hand-tuned rule table. The rule table's priorities are
costing it something, which the FTS run already saw from a different angle (18 true positives
suppressed outright by the `none` rules).

---

## What was predicted beforehand, and what actually happened

Written down before the run, because the previous experiment's prediction failed and
pre-registering removes the room to rationalise afterwards:

| | predicted | actual | |
|---|---|---|---|
| Goal 1 AUC | 0.75–0.85 | 0.862 | close, slightly under |
| Goal 1 F1 | below the 0.621 floor | **0.768, cleared it** | **wrong** |
| PRUNE | weak | AUC 0.848, +0.067 precision | understated |
| RECOVER | better than prune | AUC 0.903, 4.8x chance | right |
| meta+emb gain | +0.02–0.05 F1 | +0.027 / +0.046 | right |

The one that was wrong is the one that matters: I expected better ranking but no usable
classifier, on the grounds that precision would stay poor. Precision at the transferred
threshold is 0.667 for `emb_max6:ta` — mediocre, as predicted — but recall of 0.904 carries the
F1 past the floor anyway. The error was treating precision as the binding constraint when the
whole FTS finding was that recall is.

## Limits to state with these numbers

- **One model, not a model comparison.** `multilingual-e5-base` was chosen for Dutch retrieval
  and for running locally; nothing here says it is the best choice, and no second model was
  tried.
- **The query text is mine.** It is the task definition translated, not tuned against the
  labels, but a different six clauses would give different numbers. The `emb_one` arm is the
  honest floor for "what if the phrasing were worse".
- **`body` is truncated to 1,800 characters** before encoding (the model's context is 512
  tokens), so the `:body` arms see the opening of a 2,453-character average document. That is a
  property of the arm, not of the corpus.
- **Leakage checked, negligible:** 3 activity texts spanning 25 rows (3.6%) appear in more than
  one block, so the supervised folds are nearly disjoint. `utr-binnenstad-300` shares **zero**
  text with the other two, and it is the strongest fold, so the supervised numbers are not
  memorisation.
- **Still loud, not disturbing.** Time left the concept in the ground truth; none of this says
  anything about whether a resident will be woken up.
- **No pgvector.** 695 × 768 floats is a numpy dot product; nothing here needed an index. Doing
  it in Postgres would be a separate exercise about ANN recall and index build cost, not about
  whether the signal exists.
