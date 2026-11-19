# Embedding the body instead of the 23-token summary: the gate, and what it did not settle

2026-11-18. Branch `experiment/body-embedding`. Nothing in `api/` or `migrations/` changed.

## Why this was run before anything was built

`/search/notices` embeds `bekendmaking_activity.activity_text`, a derived keyword summary.
Measured with the model's own tokenizer over a random 1,500-row sample:

```
embedded today  "passage: " + activity_text[:1800]   median    23 tokens   p90   85   max  320
available       "passage: " + body_text              median   452 tokens   p90  796   max 4435
multilingual-e5-base window                                   512 tokens
body fits whole in one window: 56.1%      body coverage: 94,183 / 94,183
```

Switching is a ~5-hour encode and ~670 MB of index, and the frozen judgeset cannot score the
result: its pool is the depth-10 union of four arms, so a document a new arm surfaces from
outside it is scored 0 — a guaranteed underestimate, previously measured at 273 new judgments
for a far smaller change. So this run does not retrieve. It **reranks each query's
fully-judged pool** under the two representations: every ranked document carries a grade, so
there is no fill convention, no bound and no pool bias.

The availability precondition passed and the causal story did not, which is why this was a gate
and not a formality: of the 169 pool-best documents production misses, 167 have more body text
than embedded text (median 1,249 vs 165) — but those missed documents already carried **3.4x
more** embedded text than the 178 it finds (median 165 vs 48).

## Result

```
arm       POOL    MINI  dis/10
act      0.669   0.667    0.10
body     0.757   0.295    3.77

pool  mean +0.088  median +0.000  [-0.000, +0.185]  BELOW MDE   W/L/T 18/19/3
      MDE of THESE differences at n=40: 0.133 (sd 0.302)   sign test p=1.000
      top-3 wins are 76% of the total
      bootstrap lower bound > 0 in 64% of 200 RNG seeds
```

The control arm reproduces production exactly: per-query nDCG identical to 1e-9 on 40/40
queries against the recorded `vec` arm, top-10 overlap 10.00/10, re-encoded vectors at min
cosine 0.999999 against the stored ones. That agreement is an identity — every query's `vec`
top-10 lies wholly inside its judged pool, so reranking the pool with the same vectors cannot
change it — so it is real but is **not** independent evidence of encoder fidelity beyond C1/C1b.

Raw: `raw/body-repr-ab-raw.json` (md5 `88caf5a05515b4be8fe8b0b4acaccf71`),
`raw/body-repr-score.txt`. Scripts: `etl/experiments/body_repr_ab.py`, `body_repr_score.py`.

## `falsifier` DESTROYED the first write-up. Three of its objections were bugs in the harness.

**1. The headline's own interval was a different draw from the one that decided the gate.**
`boot()` took a shared `random.Random` and was called three times from `main()`, so each call
drew from a different position of one mutating stream. The same 40 differences printed
`SIGNIFICANT` (lower bound `+0.000`) for the headline and `GATE: NOT PASSED` (lower bound
`<= 0`) nine lines later. `boot()` now seeds an independent generator from its tag, and the gate
reuses the headline's draw by tag rather than taking a fresh one.

**2. `MDE = 0.030` was a constant imported from a different experiment.** It came from
`rerank_score.py`, where it was measured on a different statistic (reordering one arm's top-10,
sd ~0.068). These paired differences have sd 0.302, so the minimum detectable effect at n=40 is
**0.133** — and the wrong constant silently suppressed this script's own
`(below this set's MDE)` annotation on a result that was below it. An MDE is a property of a
measurement, never a constant to carry: it is now computed from the differences at hand.

**3. The mechanism claim was wrong in both directions at once.** The first write-up read the
subgroup table as a trade — "moves the 242 missed documents up 4.58 ranks, moves the 199 found
ones down 4.15". The subgroups are defined by the **control arm's own top-10**, so missed
documents sit at rank 17.5 of a 28-document pool and found ones at 4.2; any reordering
whatsoever moves the first group up and the second down. A uniformly random permutation of the
same pools — an arm with zero information — reproduces it:

```
subgroup                      n    act   body   delta  chance  vs chance
MISSED by production vec    242  17.50  12.92   -4.58   -4.08      -0.51
FOUND by production vec     199   4.21   8.36   +4.15   +8.93      -4.78
```

So "it moves the missed documents up" is the floor of the definition (`vs chance -0.51`,
cluster-bootstrap 95% CI `[-1.55, +1.31]`), and the found-document regression is **4.78 ranks
better than chance**, so it does not establish harm either. The null-permutation column is now
printed next to every subgroup. The partition itself does carry information — a random 242/199
split of the same 441 documents gives `-0.48 / -0.80` — the baseline is simply not zero.

Two further objections stood. The truncated-versus-whole gap (`-2.53`, CI `[-5.25, +1.17]`,
59 pairs in only 15 queries) is not distinguishable from zero, so the supportable statement is
*no evidence that truncation limits the effect*, not *the truncated subgroup moves more*. And
all four controls pass with the two arms' keys swapped and the conclusion inverted: they assert
properties of the scorer, not the sign or the provenance of the arms. They are not identities
in disguise — sabotaging the pool endpoint to read the mini ranking makes IDENTITY and RANDOM
print `FAIL` — but `MUTATION` passed having moved 0.001 against a `1e-6` threshold, which
detects nothing; its threshold is now a quarter of the endpoint's own MDE.

### What `falsifier` could not break

The control arm's reproduction of production; the single stale stored vector
(`gmb-2026-317120`, judged in 1 of 40 queries, grade 0, not in that query's top-10, and the arm
re-encodes rather than reading the table — no contamination path); and that the representation
axis genuinely varied (0/40 queries gave an identical pool ranking, 36.0% of encoded bodies
truncated, and the body-arm fallback never fired: 0 of 839 judged documents have an empty body).

## My own metric, withdrawn before `falsifier` saw it

A second endpoint added ~2,000 random corpus documents as presumed-irrelevant distractors to
make the gate recall-shaped. It printed **-0.372 [-0.468, -0.269]**, which looked like
decisive harm. The presumption was argued to be conservative — "a distractor that is really
relevant can only penalise the arm that ranks it highly" — which is true, and is exactly why
the metric is invalid: **it penalises the arm for doing its job.** Inspected immediately:

| query | the "distractor" the body arm promoted |
|---|---|
| is someone felling trees near me | `het kappen en herplanten van vier bomen` — felling and replanting four trees |
| what noisy works are nearby | `ontheffing geluidshinder` — a noise-nuisance exemption |
| will my parking space be taken | `het aanleggen van een gereserveerde gehandicaptenparkeerplaats` |

Those are correct answers that are not in the pool — the recall win being looked for, scored as
zero and reported as harm. This is the pool-bias failure the judgeset exists to remove, rebuilt
by hand inside the instrument meant to avoid it. Only the descriptive half survives, and only
descriptively: facing the same distractors, `act` promotes **0.10** of them into its top-10 and
`body` **3.77**. Whether that is gain or loss cannot be read off this run.

## An axis nobody named until `falsifier` did

**All 40 queries are Chinese and every document is Dutch** (40/40 contain CJK). The whole
result is cross-lingual, and a 452-token Dutch body against a 23-token Dutch keyword summary is
exactly the kind of representation change whose cross-lingual behaviour need not match the
same-language case — while production takes whatever the user types. No Dutch query set exists.

## SCOPE (`falsifier`'s words, verbatim)

> 2026-11-18, reranking each of the 40 **Chinese-language** judgeset queries' own fully-judged
> pool (median 28 documents, 839 judged documents, 0 of them with an empty body, 36.0% of
> encoded bodies truncated) with multilingual-e5-base: embedding `bekendmaking_body.body_text`
> truncated at 512 tokens instead of the median-23-token `activity_text` produces **no
> measurable nDCG@10 difference** — mean +0.088 but median exactly 0.000, W/L/T 18/19/3, exact
> sign test p=1.00, Wilcoxon p=0.27, 76% of the total carried by 3 of 40 queries (mean +0.023
> without them), and +0.088 is below this set's own MDE of 0.134 at n=40 (the 0.030 the script
> prints is a constant from another experiment); the bootstrap lower bound changes sign across
> RNG seeds (0 > lo in 23% of 200 seeds), which is why the run's own "SIGNIFICANT" label and
> "GATE: NOT PASSED" contradict each other. The control arm reproduces production exactly
> (per-query nDCG identical to 1e-9 on 40/40 queries, top-10 overlap 10.00/10). **No claim about
> this representation's effect on the rank of the documents production misses is supported**: a
> zero-information random permutation of the same pools moves them up 4.12 ranks against the
> body arm's 4.58 (difference -0.48, cluster-bootstrap 95% CI [-1.55, +1.31]), so the
> -4.58/+4.15 "trade" is a floor/ceiling artifact of defining the subgroups from the control
> arm's own top-10, and the truncated-vs-whole gap (-2.53, CI [-5.25, +1.17]) is not
> distinguishable from zero. Whether the body representation gains or loses **recall** remains
> untested by construction, and the gate is NOT PASSED.

## Disposition

**The gate is NOT PASSED, so Phase 1 (the full-corpus build) does not start.** The reason is
not that the body representation was shown to be worse — it was shown to make no measurable
difference *to the ordering of a pool it cannot add to*, which is a different and much weaker
statement.

The one thing this run produced that points forward is the finding that killed my own metric:
the body arm promotes out-of-pool documents at **38x** the control's rate, and the three
inspected by hand were right. That is the recall question, and it is answerable only by judging
those documents — the depth-50 pool extension this work has already priced at ~350 documents.
**Owner: unassigned**, pending that judging.

Not done: a ledger claim and verdict for this run.

---

## The pool extension this note asked for, built 2026-11-19 (NOT YET JUDGED)

The disposition above says the recall question is answerable only by judging the documents the
body arm surfaces from outside the pool. That instrument now exists:
`etl/experiments/body_pool.py` (build) and `body_pool_score.py` (score, after judging).

What it cost, measured rather than estimated: **58 minutes at 26.6 docs/s on mps** for all
94,183 bodies, into a gitignored `data/body_vectors.npz` of 295 MB. The plan's ~5 hours was
extrapolated from the title-prefix arm's token count and was wrong by 5x. Nothing was built in
the database — no migration, no HNSW index, no change under `api/` — so this step did not need
the gate to have passed: 40 brute-force matmuls over 94,183 x 768 are milliseconds, and an
approximate index would have folded its own recall loss into the arm's score, the reason
`judgeset_pool.py` turns the index off.

`judgeset_pool.py` is untouched. Re-running it needs a DEEPSEEK key and 40 LLM calls and would
overwrite `judgeset-raw.json` / `judgeset-judge_sheet.json`, which are inside the `judgeset`
path facet and pinned by `sha256` in `evidence` rows. This script only adds, into its own files.

```
body arm top-10 over the full corpus: 380 new (query, document) pairs, 20 already judged
C1 cached vector vs re-encoded, n=32: min cosine 1.000000
C4 vs production vec top-10: 0/40 identical, mean overlap 0.28/10
C3 sheet fields: doc_type, gemeente, id, n, text, title -- no grade, score, rank or arm
sheet: 390 items over 40 queries (380 to judge + 10 calibration)
```

**The two arms' top-10s are 97% disjoint** (mean overlap 0.28 of 10), which is why reranking a
pool built without this arm could not see it, and is the quantity the Phase 0 gate was blind to
by construction.

Three structural checks, run so that a confound is not discovered after judging. None of them
is a relevance judgement, deliberately — characterising these candidates before the judge sees
them would prime the judging, and the judge is the instrument:

```
corpus median body length 1771
vec   n=400  body len median 1133  p10 373  p90 2418  34 gemeente  7 doc_type
body  n=400  body len median  804  p10  64  p90 3779  59 gemeente  11 doc_type
```

- **No long-document bias.** The obvious confound for an arm that reads 452 tokens instead of
  23 is that it would favour long documents. It does the opposite: its candidates' median body
  length is 804 characters against the production arm's 1,133 and the corpus's 1,771.
- **It is less concentrated, not more.** 59 municipalities against 34, 11 doc_types against 7,
  and Amsterdam is 109 of its 400 slots against the production arm's 201. The composition also
  shifts from `omgevingsvergunning` (197 -> 92) toward `evenementenvergunning` (119 -> 214).
- **The fallback fires once.** 1 of 324 distinct candidates has an empty body and is therefore
  the activity summary in disguise; corpus-wide only 5 of 94,183 rows have no body. In the gate
  it fired 0 times, so this is the first run where the number is not zero.

**None of this has been through `falsifier`,** and none of it is a performance claim: they are
counts from `bodypool-arm.json` and one SQL query. The conclusion they are there to support —
whether the body arm gains or loses recall — does not exist yet, because the 380 pairs are
unjudged. **Owner: the judging is the user's.**

Two things to know before reading the scores afterwards:

- **Enlarging the pool lowers every arm's nDCG@10**, including the production arm's, whose
  top-10 does not change. The denominator is the ideal ranking over everything known relevant,
  and judging adds known-relevant documents. The 0.669 above and whatever `vec` scores next are
  **not comparable**: only the paired difference within one run is.
- **The 10 calibration items are a control that can fail.** They were judged on 2026-11-13 and
  are mixed into this sheet in the identical field shape, grades held in a separate file.
  `body_pool_score.py` checks them **before** it scores anything and exits rather than
  reporting if fewer than 7 of 10 agree or any item moves two grades, because a drifted judge
  produces numbers that look exactly like real ones.
