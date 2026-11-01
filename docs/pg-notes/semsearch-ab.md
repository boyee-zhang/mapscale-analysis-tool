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
