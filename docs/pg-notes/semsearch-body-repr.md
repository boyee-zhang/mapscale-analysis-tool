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

### How the 380 are answered, and the two controls that guard the answer file

`bodypool-judgments.json` is written pre-keyed with `null` in the sheet's own order, so judging
is replacing a null with `0`/`1`/`2` rather than retyping 390 identifiers. Re-running
`body_pool.py` is idempotent — the sheet's md5 is unchanged across a regeneration
(`e81429f447eaf4d7cdc4313dd5898785`) — and it will **not** overwrite an answer file that already
holds a grade.

The rubric is the one fixed before the first candidate of the 2026-11-13 session was read
(`semsearch-judgeset.md`), including the rule added during that session (`intrekking` is `1`,
not `2`). Applying a different rule here would make the 10 calibration items measure the rule
rather than the judge.

Both guards were sabotage-tested rather than asserted, because 0026 is in this repo because
0025 shipped a CHECK that could not reject anything:

- **A null is not a 0.** `int(None)` would crash, but coercing to `0` would be worse: `0` is a
  real grade meaning "a different subject", so a partial sheet would score every ungraded
  candidate as irrelevant — the exact pool-bias failure this exercise removes. The scorer counts
  nulls and exits: `390 of 390 candidates are not graded`.
- **Calibration refuses, with exit 1.** Flipping the 10 recorded grades by two levels gives
  `CALIBRATION FAILED: the two judging sessions are not one instrument`; leaving them intact
  reports `CALIBRATION 10/10 exact agreement with 2026-11-13` and proceeds. The synthetic file
  used for that test is random grades in the scratchpad, not judgments, and the numbers it
  produced are meaningless by construction — it tested the plumbing and the gate, nothing else.

---

# The judged answer, 2026-11-21: the body representation is WORSE, and both of my instruments were broken first

585 items judged (380 body arm + 199 control - 4 shared + 10 calibration). `falsifier` DESTROYED
the result twice. Both times the retrieval was sound and reproducible and the **instrument** was
the thing at fault, and both faults were mine.

## What may be carried, and what may not

```
arm        nDCG@10  (pool-complete, 40 queries, pool 1,085 -> 1,660)
vec        0.589
body       0.504

paired  mean -0.085  median -0.130  [-0.192, +0.031]  BELOW MDE  W/L/T 11/26/3
        MDE of THESE differences at n=40: 0.161 (sd 0.363)   sign test p=0.020

body arm top-10 (new)              n=380  relevant 240 (63.2%)
production vec ranks 11-15 (new)   n=199  relevant 137 (68.8%)
```

**Only the sign and the median survive.** Not the mean, not the pair, not `p=0.020`, not "it
gains no recall" — see the `SCOPE` below for the measurement behind each refusal. The rank-level
evidence is what is solid, and it is a much larger margin than anything the paired nDCG argues
about: **body precision@rank-1 is 47.5% against the production arm's 77.5%**, and the body arm's
precision *rises* across its own top-10 (ranks 1-5 60.5%, 6-10 67.5%) where the production arm's
falls (73.0%, 66.0%). Its score ordering carries little relevance signal inside its own top ten.

**Phase 0's 38x is dead as evidence about the representation.** 199 of 200 of the production
arm's own ranks 11-15 lay outside the 1,085-judgment pool — 99.5%. Depth-10 pool shallowness is
a fact about the pool, and any arm's next few ranks would have shown it. Without the control
band mixed into this sheet I would have reported 62.1% out-of-pool relevance as a win.

## Defect 1: the judging display was cut asymmetrically, against the arm under test

The sheet carried `activity_text[:300]`; the viewing script then cut again at 150, and that is
what was actually read.

```
cut at 150: body 200/380 (52.6%)  calib 4/10 (40.0%)  ctl 2/195 (1.0%)
cut at 300: body  98/380 (25.8%)  calib 3/10 (30.0%)  ctl 0/195 (0.0%)
```

The body arm prefers documents with longer summaries, so the success predicate stopped being
"this publication is about the thing asked" and became "its first 150 characters say so" —
which is **the control arm's own retrieval input**. A judging harness that truncates is part of
the instrument even when it lives in a scratch file.

Fixed by re-grading **all 206 affected items** (not a sample), blind, own seed, no arm, no rank
and not the previous grade shown, from the full `activity_text` — deliberately **not** the body,
because showing it credits what the rubric forbids crediting and would make the calibration
items a different task.

```
body   re-graded  200  changed   6 (3.0%)  net +8
ctl    re-graded    2  changed   0 (0.0%)  net +0
calib  re-graded    4  changed   1 (25.0%)  net +1
headline -0.093 -> -0.085; not one rank statistic moved
```

`falsifier` built the control I said I lacked — 40 blind re-grades of *untruncated* items —
and it clears me of the obvious worry and convicts me of a subtler one: inter-judge
disagreement is **15% on body items and 20% on control items** (z=-0.416, p=0.677), so there is
no upward drift hiding in "re-grade only the truncated ones", and equally the six changes are
well inside label noise. They are a correction of a known defect, not a detected signal.

**The residue it found is the part my fix cannot reach.** Of its 20 sampled body-arm zeros, 2
are documents whose subject is absent from `activity_text` **altogether** and present only in
the body: `gmb-2026-216076`'s full summary ends at `De aanvraag betreft:` — colon, nothing after
it — while the 933-character body names a triathlon championship; `gmb-2026-412602`'s is a title
plus `Algemene informatie` while its 1,080-character body names a Chinese Mid-Autumn festival.
The rubric's "graded on its title alone ... 'might be relevant if I could see inside it' is not
partial relevance" **requires** both to be 0. At that measured 10% the mean is -0.062; at the
top of its interval, -0.019. So the rubric that keeps the comparison honest also handicaps the
treatment, and that is not fixable inside this instrument — it is a different instrument.

## Defect 2: archiving the build log destroyed the evidence chain

Worse than the first, and entirely structural. `rng = random.Random(SEED)` was shared;
`control_alignment` consumes `rng.sample(range(94183), 32)`; so `--skip-control` did not skip a
check, it **shifted every later draw**. The run whose only purpose was to archive the build log
redrew the ten calibration pairs to a disjoint set with none of them graded — 30 seconds after
the score was archived — and the cited scoring command then refused with `CALIBRATION FAILED`.
The figures were reachable only under `--allow-drift`, and no committed or on-disk state
reproduced them.

That is the same bug family as `body_repr_score.py`'s `boot()` earlier in this work, where three
calls drew from one mutating stream and the headline contradicted the gate. Twice now.

Recovered, not reconstructed: running the build **without** `--skip-control` restores the
original draw exactly, and all 10 calibration items are graded in both passes. Three fixes so it
cannot recur:

- **One generator per consumer, seeded from a tag** (`{SEED}:control`, `:calibration`,
  `:sheet`), so no flag can reach the sampling.
- **The key is an input once grades exist**, not an output. Redrawing it replaces the control
  with ten pairs the judge never saw.
- **The sheet and the key are write-protected like the answer file.** Protecting only the grades
  was protecting the cheapest of the three: the sheet is the only record of what the judge was
  shown, and the 52.6%-against-1.0% defect above is a measured quantity that lives in it.
  `falsifier` could only confirm that defect by going round the overwritten file to the
  database, which works solely while `activity_text` is unchanged — and that is derived state
  `probe_corpus` watches precisely because it moves.

Both are now in git, which they were not: the judged 585-item sheet existed in no commit.

Verified: `--skip-control` and a full run both leave the key at md5
`277431db256695706d57b2e72397e05a` and the sheet at `cd387f6291adebd76b43513f6c2cd482`, and the
cited command reproduces without `--allow-drift`.

## Calibration, stated rather than passed over

**8/10**, not 9/10 — the re-grade moved one calibration item 1 -> 2, and 4 of the 10 were
themselves truncated at 150. The control built to show the two judging sessions were one
instrument was itself ~40-50% degraded by the display defect it was supposed to be blind to. At
2 disagreements of 10, a true 30% per-item disagreement rate survives at p=0.383, and 8/10 is
one item above the script's own `agree < 7` refusal threshold.

## SCOPE (`falsifier`'s words, verbatim)

> Measured 2026-11-21 on the 40-query judgeset over the 94,183-publication corpus
> (`activity_derived AND activity_text <> ''`; population identical to
> `bekendmaking_embedding` with 0 rows differing either way), `intfloat/multilingual-e5-base`,
> exact non-ANN retrieval, depth-10 pooling, pool 1,085 -> 1,660 judgments, labels
> `bodypool-judgments-v2.json` (the 206 items whose summary was cut at 150 characters re-graded
> from the full summary): embedding `bekendmaking_body.body_text` truncated at 512 tokens
> (binding on 43.9% of bodies; median body 456 tokens against 24 for
> `"passage: " + activity_text[:1800]`) ranks WORSE than the production `activity_text`
> representation. **Only the sign and the paired median may be carried: median -0.130, W/L/T
> 11/26/3.** The sign is robust — negative in 2,000 of 2,000 reps of a label-resampling
> bootstrap drawn from a measured inter-judge transition matrix (grade 1 is read as 0 by an
> independent blind judge 33% of the time; grades 0 and 2 are 92% and 100% stable), negative
> across leave-one-query-out (-0.112 … -0.067), and stronger under symmetric trimming
> (3-trimmed -0.115 against a trimmed MDE of 0.111). **The mean -0.085 and the pair 0.504
> against 0.589 may NOT be stated**, for three separately measured reasons: the mean is below
> this set's own MDE of 0.161 and fails a sign-flip permutation test at p=0.145; label
> resampling puts it at -0.076 [-0.106, -0.043]; and the corrected label set still scores as 0
> the documents whose subject is absent from `activity_text` altogether and present only in the
> body — 2 of 20 sampled body-arm zeros (10%, exact 95% CI 1.2%-31.7%), including a
> 294-character summary that ends at "De aanvraag betreft:" with nothing after the colon — which
> the rubric's "graded on its title alone" clause requires and the full-summary re-grade cannot
> reach; upgrading those at the measured 10% gives -0.062 and at the top of their interval
> -0.019. **"exact sign test p=0.020" may NOT be stated either**: under the same label
> resampling its median is 0.065 and it clears 0.05 in 49.6% of reps, so it describes one
> realisation of the labels, not the measurement (Wilcoxon p=0.038 is the same statistic's
> edge). **"It gains no recall" is NOT established**: 63.2% against 68.8% out-of-pool relevance
> and a paired per-query yield difference of -0.050 against the script's own MDE of 0.174; the
> recall question is open, not answered negatively. What is established independently of the
> paired nDCG and is a much larger margin: the body arm's precision@rank-1 is 47.5% against the
> production arm's 77.5%, its precision rises across its own top-10 (ranks 1-5 60.5%, ranks
> 6-10 67.5%) where the production arm's falls (73.0%, 66.0%), and its own ranks 1-5 are 58.2%
> relevant against the control band's 68.8% (p=0.029) — so the body arm's score ordering carries
> little relevance signal inside its own top ten. Also established mechanically, and not in
> doubt: 199 of 200 of the production arm's own ranks 11-15 lay outside the 1,085-judgment pool,
> so depth-10 pool shallowness is a fact about the pool and the earlier Phase 0 gate's 38x
> out-of-pool promotion rate is not evidence about the body representation. The retrieval side
> is sound and reproducible — all 40 queries' top-10 reproduce byte-identically from
> `data/body_vectors.npz`, C1 re-run independently gives min cosine 1.000000 against a
> one-row-roll sabotage control at 0.789584, and the arm is identical to the committed one.
> **The evidence chain is not**: the calibration key was rewritten 30 seconds after the archived
> score by `body_pool --skip-control` (shared `random.Random(20261119)`; skipping
> `control_alignment`'s 32-sample shifts the calibration draw), to ten pairs with zero overlap
> and none graded, so the cited scoring command refuses with `CALIBRATION FAILED` and the
> archived figures are reachable only under `--allow-drift`; HEAD's copy of the key also leaves
> 2 of 10 ungraded, so no committed or on-disk state reproduces either 0.496 or 0.504; the
> 585-item judge sheet that documents the 52.6%/1.0% display defect was overwritten in the same
> run and exists in no commit (HEAD's copy is a 390-item pre-control-arm build), and its cut
> rates could only be reconstructed from `bekendmaking_search_text`, which works solely while
> `activity_text` is unchanged; and `migrations/0034_bodypool_facet.sql`, applied, pins
> `bodypool-calibration_key.json` with the comment "if these move, the two sessions are not one
> instrument and the comparison is not either" while also pinning `body_pool.py`, which
> unconditionally rewrites that file and `bodypool-arm.json` on every run. Until a calibration
> key is restored that the v2 grades actually cover, this result is a direction supported by
> rank-level evidence, not a recorded measurement.

The last sentence is the condition this note was held against: the key is restored, the v2
grades cover all 10 of its pairs, and the cited command reproduces without `--allow-drift`. The
`SCOPE`'s other refusals stand as written.

## Disposition

- **Phase 1 does not start, and now for a measured reason rather than a null one.** The gate
  found no difference; the judged comparison finds the body representation ranks worse, in a
  direction robust to label resampling, leave-one-out and trimming.
- **The recall question is still open.** It was not answered negatively. Answering it needs
  relevance labelled from the body, which is a different instrument: the calibration items were
  judged summary-only on 2026-11-13, so a body-visible session would have to redraw them first.
- **The 23-token summary is not the bottleneck it looked like.** `MAX_CHARS = 1800` never bites
  at a median of 24 tokens, and the body does not beat it. What the rank-level numbers point at
  instead is that e5 embeddings of a 456-token Dutch administrative body do not order Chinese
  queries' results well — precision@1 47.5% against 77.5%, rising rather than falling across
  its own top-10. **Owner: unassigned.**
- **All 40 queries are still Chinese and every document Dutch.** Unchanged from the gate, and
  it bounds everything above.

Ledger: claim 33, verdict recorded against `corpus,judgeset,bodypool` (0034 declares the last).
Raw: `raw/bodypool-{build,score,score-v2,yield,yield-v2}.txt`,
`raw/bodypool-regrade_grades.json`, `raw/bodypool_{yield,regrade_build}.py`.

### Two evidence rows are stale, and that is the third instance of one lesson

```
evidence 26  STALE  bodypool-build.txt     <- the --skip-control run that broke the key
evidence 29  STALE  bodypool-yield.txt     <- re-archived when the yield script took an argument
evidence 27,28,30,31,32,33  ok
```

I re-archived over both files after fixing the defects, so the `sha256` in `evidence` no longer
matches the bytes at the path — exactly what §1b says that digest exists to catch: "a path with
no digest still points at something afterwards, so the record reads as supported when the bytes
are gone." Here the digest did its job and the record reads as *unsupported*, which is correct.
The run evidence 26 describes is the one that destroyed the calibration key; its output is gone,
and evidence 33 is the same command after the fix.

**Three instances of one lesson in one session, each a layer further out.** A shared
`random.Random` across three `boot()` calls (contradictory intervals). A shared
`random.Random` across the control and calibration draws (a flag silently redrew the control).
And now: re-running an archive step over the file a recorded digest points at. The fix for the
first two is in the code; the fix for the third is that **a raw output file is append-only in
practice — a re-run gets a new name, not the old one.** Not enforced. **Owner: unassigned.**
