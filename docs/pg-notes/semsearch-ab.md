# Semantic search over `bekendmaking`: can we skip pgvector?

Measured 2026-11-01 against the live table (61,853 rows: Amsterdam 53,712 + Utrecht 7,287 +
Diemen 848, 2026 only). Raw data in `semsearch-ab-{raw,judge_sheet,judgments}.json`; harness in
`scripts/experiments/semsearch_ab.py`, re-runnable.

## Why we were here at all

The goal under discussion is turning this project into a natural-language geo-query system —
the shape of it being *"Find restaurants within 2km of Amsterdam Central Station, preferably
near a park"*: parse the constraints, resolve the anchor, query PostGIS, rank semantically,
explain the result in prose. Assessing that against the code produced three findings before any
experiment was run, and they are what made this experiment the right next step.

**1. That example query needs no vector search at all.** `park` and `restaurant` are both
already among the 34 rows of `poi_category` (`migrations/0004_poi_category_seed.sql`), and
"preferably" is an `ORDER BY`, not a `WHERE`. The LLM's real job is to split a sentence into
hard constraints (compiled to `WHERE`) and soft ones (compiled to `ORDER BY`) — not to embed
anything.

**2. Vector search over `poi` would have no corpus.** A `poi` row is `name` + `category` +
`tags`. Embedding "Café Loetje" + "cafe" yields nothing that `category = 'cafe'` does not
already give exactly, and approximate nearest-neighbour would actively mix `fast_food` into
`restaurant` results. Where a concept space is already enumerated, semantic retrieval has no
work to do.

**3. `bekendmaking` is where the text actually lives** — and its taxonomy is useless, which is
the opposite of `poi`:

| | |
|---|---|
| `andere vergunning`, a bucket literally named "other permit" | **53%** of rows |
| rows with no `activiteit` tag at all | **69%** |
| `Besluit apv vergunning ...` template titles | **36.7%** |

(Correcting a claim made earlier in the same discussion: `doc_type` has **15** distinct values
in this corpus, not 100+. The 100+ is the upstream SRU vocabulary, not what was imported. The
real argument for semantic search here is not that the taxonomy is too large to enumerate — it
is that the taxonomy is small and carries almost no information, so the only place the subject
matter exists is the free text in `title` and `abstract`.)

The `abstract` rescues the template rows, but in jargon: `TVM parkeervak` (12,301 rows) where
TVM = *Tijdelijke Verkeersmaatregel*, `Object` (7,462), `Nachtwerk` (111), `Filmen` (350),
`RVV` (178). An embedding model almost certainly does not know "TVM". Whether an **LLM** knows
it is a testable question — and that is the experiment.

## The actual question

Adding pgvector means a new extension, a new column, an embedding ETL over 61,853 rows, and
either an API dependency or a 2 GB image (`sentence-transformers` + `torch`; DeepSeek publishes
no `/embeddings` endpoint, verified against its API docs). Before paying any of that:

> The NL-parsing layer has to call an LLM **once anyway**. If that same call is shown the
> corpus vocabulary and asked to expand the question into terms that actually occur, the
> marginal cost is **zero new dependencies, zero new columns, zero ETL**. Is that good enough?

## Design

Both arms share the retrieval engine (`to_tsvector('dutch', title || ' ' || abstract)`,
`ts_rank_cd`, `LIMIT 10`) and make exactly **one** LLM call (deepseek-chat, temperature 0).

| | |
|---|---|
| **A_literal** | LLM translates the question to Dutch keywords. No corpus context. |
| **B_expanded** | LLM is shown the measured corpus vocabulary (15 doc_types, 9 activiteiten, top-30 abstract prefixes with row counts) and asked to expand into terms that actually occur, plus optional `doc_type` / `activiteit` hard filters. |

**The only axis varied is whether the LLM has seen the corpus vocabulary.** Same engine, same
call count, same 15 Chinese questions a resident would actually ask.

Scoring: results from both arms pooled per query, deduped, shuffled, provenance stripped, then
judged by relevance. 285 candidates, 160 judged relevant. Overlap between the arms was only
**10% (15/150)**, so the pool is genuinely two different document sets.

## Result

| | precision@10 | recall over pool |
|---|---|---|
| A_literal | **69/150 = 46.0%** | 43.1% |
| B_expanded | **105/150 = 70.0%** | 65.6% |

Per-query: **B wins 8, A wins 2, tie 5.**

Ties are informative. `我家附近有人要砍树吗` is 10/10 both ways because `kappen` is
simultaneously the literal translation and the corpus term — expansion has nothing to add
when the user's word is already the corpus's word.

A's two wins came from B over-constraining: on `有人要把一套房子拆成多套出租吗` B added
`doc_type=['omgevingsvergunning']` + `activiteit=['bouwen']`, which excluded the
`kadastraal splitsen` rows that are the real answer. **A hard filter the LLM guessed is worse
than no filter.**

## Three queries scored 0/10 on BOTH arms — and the answers exist

| query | B produced the right term | rows in corpus | B hits |
|---|---|---|---|
| 充电桩 (EV charging) | `oplaadvakken` ✓ | 696 | **0/10** |
| 自行车 (bicycle) | `fietsen` ✓ | 312 | **0/10** |
| 垃圾箱 (skip on street) | `afvalcontainer` ✓ | 14 (+11,284 `Object`) | **0/10** |

The term extraction was correct and retrieval still returned nothing relevant. Diagnosed:

**`ts_rank_cd` does no IDF weighting.** In an OR query `t1 | t2 | ... | tn`, a document matching
only a *broad* term scores the same as one matching the *precise* term. Measured directly:

- `to_tsquery('dutch','oplaadvakken')` alone → correct documents rank **1-4**.
- B's full OR query (which included `omgevingsvergunning`) → match set explodes to **17,262
  rows** and the same documents land at rank **10, 100, 102** — all with **identical score
  0.20000**.

Identical scores across clearly different relevance means the ranking function is not
discriminating at all; the tie is then broken by the `ORDER BY published_at DESC` tiebreaker,
which is random with respect to relevance.

Root cause: **B put `omgevingsvergunning` and `verkeersbesluit` in BOTH `terms` and `doc_type`.**
Those are structural facets, not content words. Putting a facet in the OR term list poisons the
rank for every other term in it.

## The fix, and what it did and did not fix

Stripping facet words out of the term list (same LLM output, same engine, nothing else
changed) — `scripts/experiments/semsearch_ab_facet_strip.py`:

- **充电桩: 0/10 → 10/10.** All ten are `aanleg twee elektrische oplaadvakken` /
  `opheffen elektrische oplaadvakken`. Completely fixed.
- **垃圾箱: 0/10 → 0/10.** Same disease, different word: `tijdelijke verkeersmaatregel` is
  itself broad (hundreds of `instellen diverse tijdelijke verkeersmaatregelen` rows) and buries
  the 14 `afvalcontainer` rows. A hand-written stop list cannot be complete.
- **自行车: 0/10 → 0/10.** Different disease: B's list contained the correct `fietsen` but also
  three unrelated `gehandicaptenparkeerplaats` phrases copied out of the vocabulary dump, and a
  4-word phrase outranks a 1-word match. **That is a prompt defect, not a ranking defect.**

So three distinct causes with three different fixes. The one that generalises:

> **Compute each candidate term's document frequency and drop or down-weight the terms above a
> threshold.** That is exactly the IDF `ts_rank_cd` is missing, it is one cheap SQL query per
> search, and it subsumes both the hand-written stop list and the broad-phrase case.

## Validity threats, stated

1. **I judged relevance myself, and the same model family did the expansion.** Judging was blind
   (shuffled, provenance stripped) and every judgment is recorded in
   `semsearch-ab-judgments.json` so it can be reviewed or overridden. It is still not an
   independent assessor.
2. **The facet-strip fix has no valid overall number.** It returned 93 documents that were never
   in the pool, so scoring it against the old pool counts them as irrelevant by default — a
   guaranteed underestimate, and the reason the naive re-score printed a meaningless 28.7%.
   Only the three zero-queries above were re-judged. A valid overall figure needs the new
   documents judged.
3. **Scale of validity: 61,853 rows, three municipalities, 2026 only, 15 queries.** The ranking
   pathology is a property of the query shape, not the row count, so it will not improve with
   more data. The precision figures will move.
4. Latency was not measured. The GIN index `bekendmaking_fts_tmp` was created for this
   experiment (5 MB, 1.6 s) and is **not** in any migration.

## What this says about pgvector

Expansion at 70% precision@10 with zero new dependencies is a real baseline, and the single
largest loss in the experiment was a **ranking** bug, not a *semantic* one — embeddings would
not have fixed it, because an embedding query OR-ed with broad terms has the same problem.

Fix the IDF gap first and re-measure. Embeddings earn their place on what is left: ranking by
degree of similarity, and the `abstract` free text where the right term cannot be enumerated.

## Where this stopped (2026-11-01)

Settled:

- Vocabulary-aware expansion is **worth having**: 70% vs 46% precision@10, 8 per-query wins to 2.
- It costs **nothing new** — the LLM call already exists in the NL layer.
- A hard filter the LLM guesses is a **liability**, not a bonus (it produced both of A's wins).
- The biggest single loss is a **ranking** defect, not a semantic one. Embeddings would not
  have fixed it.

Open, in the order that matters:

1. **Implement the document-frequency fix and re-measure.** Drop or down-weight expansion terms
   above a DF threshold, instead of the hand-written stop list proved incomplete above. This is
   the one change that could move the number materially, and until it lands the 70% is a floor
   with a known bug under it.
2. **Re-judge the 93 pooled-out documents** so the fix has a valid overall figure. Without this
   there is no honest before/after.
3. **Decide on `bekendmaking_fts_tmp`** — the 5 MB GIN index this experiment created by hand.
   Either promote it to a numbered migration or drop it. It is currently in the local database
   and in no migration, which is the worst of both.
4. Only then: decide whether pgvector earns its place on what expansion still cannot reach
   (ranking by degree of similarity, and `abstract` free text).

Not addressed here, and still true: the corpus is three municipalities and 2026 only, so any
evaluation set built from it measures retrieval, not coverage. Widening it is
`scripts/import_bekendmaking.py --gemeente X`, one municipality at a time; there is no national
mode.

---

# Follow-up: was it "did not find" or "found but ranked low"?

Measured 2026-11-02, before touching any embedding API, on the advice that the question
should be answered first. Harness: `scripts/experiments/semsearch_ab_diagnose.py`, output in
`semsearch-ab-diag.json`. No new judging — it re-runs each arm's own tsquery with **no limit**
and the same `ORDER BY`, then asks where the already-judged relevant documents actually land.

## Result: it is ranking, not recall

| | known relevant | query matches it at all | of matched, reach top-10 |
|---|---|---|---|
| A_literal | 160 | **131 (82%)** — misses 29 | **67/131 = 51%** |
| B_expanded | 160 | **128 (80%)** — misses 32 | **105/128 = 82%** |

Two things follow, and the first one is the surprise:

1. **Expansion did not improve raw recall at all.** A misses 18% outright, B misses 20% —
   indistinguishable, and B is nominally *worse*. Whatever B is buying, it is not reach.
2. **The entire 46% → 70% gap is ranking.** A found 131 relevant documents and buried 64 of
   them below rank 10. B found 128 and buried 23.

## The mechanism: match-set size predicts failure

| match set | share of matched relevant docs reaching top-10 |
|---|---|
| < 1,000 | **85%** (n=8) |
| 1,000–10,000 | 79% (n=10) |
| **> 10,000** | **42%** (n=7) |

Spearman rho = **-0.63**, Pearson r = -0.72, n = 25 (query × arm pairs with at least one
matched relevant document).

A's worst cases have match sets of 44,773 / 44,060 / 43,689 / 22,193 — one literal Dutch word
matching most of the corpus. B's best have 54 / 332 / 680. So **the predictor of failure is
how many documents the query matches, not how good the terms are** — which is the same
`ts_rank_cd`-has-no-IDF finding from the first experiment, now with a dose-response curve
instead of a single anecdote.

Caveats on this number: n = 25 is small, and match-set size is partly confounded with the arm
(A tends to produce broader terms). The bucket table pools both arms, so comparisons *within* a
bucket are fair, but the correlation should be read as direction-and-rough-magnitude.

## The blind spot this measurement cannot fill

The relevant set is **pool-derived** — it was built from what the two arms surfaced in their own
top-10. So a document that *neither* arm ever surfaced is not in the 160 and contributes
nothing. The 696 `oplaadvak` rows are the known example: for that query the relevant count is
literally 0, because nothing correct was ever retrieved to judge.

**So this experiment cannot size the recall gap.** It can only say that among documents at
least one arm reached, the loss is ranking.

## What this means for embedding, stated as two separate hypotheses

- **"Embedding fixes recall"** — neither confirmed nor refuted. The blind spot above is exactly
  where it would show up, and sizing it needs real ground truth for a fixed concept, not
  pooled judging.
- **"Embedding fixes ranking"** — substantially strengthened. Ranking is the confirmed cause of
  loss, and a cosine distance is a continuous, discriminating score where `ts_rank_cd` returns
  a constant.

But the second hypothesis has a cheaper competitor that must be measured first: **a
document-frequency weighting on the existing FTS**. Embedding's opponent on ranking is not
today's `ts_rank_cd` — it is a *fixed* `ts_rank_cd`. That fix costs one SQL query per search
and no new dependency, and the dose-response curve above predicts it should recover most of
the loss on its own.

## Still unseparated, and cheaper than any of this

Arm B changed **two** things at once: it was given the corpus vocabulary *and* told to expand.
The 24-point gap cannot be attributed between them. Two LLM calls settle it — give A the
vocabulary but ask only for translation, give B no vocabulary but ask for expansion. If the
vocabulary is the main effect, then the load-bearing component is an **offline term-frequency
table**, not a model, and the expected value of the whole AI track moves down.

---

# Follow-up 2: separating vocabulary from expansion — and finding out n=15 cannot attribute

Measured 2026-11-02. The first experiment's arm B changed **three** things at once relative to
arm A: it was given the corpus vocabulary, it was told to expand, and its output schema allowed
`doc_type`/`activiteit` hard filters. This runs the missing cells of the factorial with the
schema held at terms-only, and extends the judgment pool properly.

Harness: `scripts/experiments/semsearch_factorial.py`, `_score2.py`, `_score3.py`.

| | no vocabulary | vocabulary |
|---|---|---|
| **translate only** | A (reused) | **C (new)** |
| **expand** | **D (new)** | B' = B with its filters discarded |

234 documents returned by C and D were not in the original pool. **All of them were judged
blind** before scoring, and every arm was then re-scored against the extended pool — that is
the step whose absence invalidated the facet-strip number in the first experiment.

## Scores on the extended pool

| arm | precision@10 |
|---|---|
| B with filters (the original arm B) | **105/150 = 70.0%** |
| C vocabulary + translate | 95/150 = 63.3% |
| B' vocabulary + expand, no filters | 91/150 = 60.7% |
| D expand, no vocabulary | 86/150 = 57.3% |
| A translate, no vocabulary | 69/150 = 46.0% |

Arm A scores exactly 69/150 on both the original and the extended pool, which is a useful
sanity check: extending a pool can only add relevant documents, and none of A's results were
newly judged relevant.

## The real finding: the total effect is real, the attribution is not

Paired bootstrap on the 15 per-query differences, 20,000 resamples:

| comparison | mean diff (hits/query) | 95% CI | |
|---|---|---|---|
| **A vs B-with-filters** | **+2.40** | **[+0.67, +4.20]** | **significant** |
| vocabulary, holding translate (C − A) | +1.73 | [−0.93, +4.33] | inside noise |
| expansion, holding no vocabulary (D − A) | +1.13 | [−0.73, +3.13] | inside noise |
| expansion, holding vocabulary (B' − C) | −0.27 | [−3.00, +2.33] | inside noise |
| the hard filters (B − B') | +0.93 | [−0.60, +2.67] | inside noise |

So the headline of the first experiment survives — something in arm B really does work. But
**n = 15 cannot say what.** Every decomposition of that 24-point gap crosses zero.

The point estimates are suggestive and no more: vocabulary looks like the larger lever (+17
points on its own), expansion adds nothing once the vocabulary is present (−2.7, interaction
−14.0), and the best single arm is vocabulary + *plain translation* — the cheaper prompt. If
that pattern held, the load-bearing component would be an **offline term-frequency table**
rather than a model. It is a hypothesis, not a result.

**Correction to an earlier claim in this file:** "a hard filter the LLM guessed is worse than no
filter" was drawn from the two queries where it visibly hurt. In aggregate the filters are
+0.93 per query and the filtered arm is the best of the five. That claim was an overreach.

## How many queries would it take

Paired t, alpha = .05, power = .80, using the measured sd of per-query differences (5.39):

| effect to detect | ≈ percentage points | queries needed |
|---|---|---|
| 2.5 hits/query | 25 | 36 |
| 1.7 | 17 | **79** |
| 1.0 | 10 | **228** |
| 0.5 | 5 | 912 |
| 0.3 | 3 | 2,532 |

**A 15-query set can only see differences of 25 points or more.** Embedding's plausible gain
over a *fixed* FTS is far more likely to be 5–10 points, which needs 228–912 judged queries.
On that path the most likely outcome is no conclusion at all — neither "it works" nor "it does
not" — which is the worst of the three.

## Why this validates narrowing to a single concept

Fixing the concept ("noisy works") changes the error structure, not just the scope:

- **Per-query means over 15 queries**: n = 15, sd of differences 5.39, only huge effects visible.
- **Recall against near-exhaustive ground truth for one concept**: the unit becomes *the
  document*, not the query. The question is "did method X retrieve document d", a proportion
  over the few hundred relevant documents inside 61,853 — and the corpus is templated enough
  (9,563 distinct 40-char title prefixes; `TVM parkeervak` alone covers 12,301 rows) that
  labelling *patterns* rather than rows makes near-exhaustive truth feasible.

That is a confidence interval an order of magnitude tighter, for less labelling work than
228 queries would need. The narrowed scope is not a smaller experiment; it is the only version
of this experiment that can reach a verdict.

## Incidental findings from the 234 newly judged documents

Worth recording because they bear on the recall question the pool-based method cannot answer:

- Three queries that scored **0 relevant** in the first experiment have real answers that no
  original arm retrieved: `充电桩` → 10 (`aanleg twee elektrische oplaadvakken`), `垃圾箱` →
  13 (`Melding objecten ... het plaatsen van een container op de openbare weg`), `自行车` → 5
  (APV rows whose abstract starts `Fietsen`). **The first experiment's relevant sets were
  missing documents that exist in quantity** — direct evidence that pooled judging understates
  recall.
- Whole vocabulary families were absent from the original pool: `vellen van een houtopstand`
  (the formal register for felling, vs Amsterdam's `kappen`), `terrasvergunning`,
  `horecavergunning`, `omzettingsvergunning`, `kamerverhuur`, `filmvergunningen`,
  `Melding objecten`.
- A false-positive class worth knowing: `Besluit apv vergunning Verleend - **Bouwen**
  Ewoutszstraat` matches a query for `bouwen` because **the street is called Bouwen
  Ewoutszstraat**. Street names in the title poison a content-word search; the address belongs
  in `bekendmaking_locatie`, not in the indexed text.
- `maatwerkvoorschrift bouwlawaai/werktijden ... geluidhinder sloopwerkzaamheden` exists — a
  document type that is *literally* about construction noise, and which none of the earlier
  noise queries surfaced.
