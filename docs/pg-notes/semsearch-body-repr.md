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
