# Semantic search over 94,183 publications — built and measured 2026-11-05

Free text in, publications ranked by meaning out, optionally clipped to an isochrone. This is
what the embedding work had been heading toward, and it retires the standing caveat that every
query clause in `noise-embedding.md` was written by me: the user supplies the query now.

## 1. It works, and the cross-lingual case is the proof

English query, Dutch corpus, no translation layer and no shared keyword:

| query | top hits |
|---|---|
| `pile driving and demolition work` | `ontheffing geluidhinder bouwwerkzaamheden` 0.832, `uitvoeren van funderingsherstel` 0.825 |
| `night time music festival` | `Muziekfestival` 0.847, `Zomerfestijn Kultuureiland` 0.853 |
| `kappen van bomen` | `kappen van een boom` 0.894 |
| `markten en braderie` | `Markt en braderie in het centrum` 0.893 |

`intfloat/multilingual-e5-base`, 768-d, documents encoded `passage: …`, queries `query: …`.
The asymmetric prefixes are load-bearing and silent when wrong, so they live in one place each
and name the other in comments.

## 2. Three instrument failures, which is the real content of this note

Every performance number below was wrong at least once. All three failures shared a shape:
**a switch that did not switch, reporting a clean run.**

1. **A concurrent writer.** The first timings (4.5 s per query) were taken while the embedding
   backfill was writing to the same instance — `I/O Timings: write=358 ms`, `written=3445`.
   The monitoring notes already cost us this once. Discarded, re-measured on a quiet database.
2. **`SET LOCAL` outside a transaction does nothing.** The "exact" arm of the first
   HNSW-vs-exact comparison ran *with* the index, so the two arms trivially agreed and
   reported **recall@20 = 100%**. The giveaway was the exact arm at 1.3 ms — a sequential pass
   over 389 MB cannot be 1.3 ms, and no plausible per-row cost supports that number. The real
   figure is 72.5%.
3. **`hnsw.ef_search` does not exist until the extension is loaded into the session**, and
   worse: raising it raises pgvector's cost estimate until the planner **abandons the index
   entirely**. An ef_search sweep showed 214–227 ms at every setting — identical to the exact
   baseline, because every row of it was a sequential scan. You ask for more accuracy and
   silently get no index at all.

The fix adopted after the third: **print the plan with every measurement**, not before the
run. Every table below was produced that way.

## 3. What was measured, on a quiet database, first run discarded

94,183 vectors, **389 MB**, against `shared_buffers=128MB` — three times the buffer pool, which
is the regime where index behaviour is visible rather than hidden by cache.

| query shape | cold | warm |
|---|---|---|
| corpus-wide exact scan | **7,186 ms** | 218–300 ms |
| inside a 10-min isochrone, exact (4,221 candidates) | 72 ms | **39–42 ms** |
| corpus-wide, HNSW (ef_search 40) | 48 ms | **4–7 ms** |

HNSW build on 94,183 × 768: **74.6 s**, `CREATE INDEX CONCURRENTLY`.

**The product's main query does not need the index.** The spatial predicate runs first and
leaves a few thousand rows; an exact scan over those is 40 ms, and the index measured no
faster (37–70 ms) because there is nothing left for it to prune.

**The corpus-wide query does.** Not for the 218 ms warm figure but for the **7,186 ms cold**
one: at 3x the buffer pool a sequential pass is mostly disk, and the first user after a restart
pays all of it. This project's own rule is to discard the first run when measuring — the user
does not get to.

## 4. The approximation is not free, and the obvious metric overstates the harm

ef_search swept with `enable_seqscan=off` so the planner could not escape the index, plan
verified on every row, 8 real encoded queries against exact top-20 as truth:

| ef_search | recall@5 | recall@10 | recall@20 | worst query @5 | median ms |
|---|---|---|---|---|---|
| 40 (default) | 70.0% | 73.8% | 72.5% | 0% | 6.7 |
| 100 | 72.5% | 76.2% | 80.0% | 0% | 4.3 |
| 800 | 85.0% | 90.0% | 93.8% | 20% | 11.1 |
| exact | 100% | 100% | 100% | 100% | **218** |

Even at 20x the default, one query in eight loses most of its true top-5. That looks damning.

**It mostly is not, and the reason matters.** The worst query is `vergunning voor een terras`,
at 0% recall@5. Its results:

| rank | exact | HNSW |
|---|---|---|
| 1 | `besluit toegekend voor het aanvragen van een terras…` | `Verleende exploitatievergunning…` |
| 2 | `Aanvraag Terrasvergunning Gerrit Rietveldsingel 17` | `Aanvraag horecavergunning met terras, Brisa` |
| 3 | `Aanvraag Terrasvergunning Gruttoplein 8` | `Aanvraag horecavergunning, CASA Damstraat; nieuw terras` |

Both lists are terrace permits. The corpus holds thousands of near-identical ones, so "the
exact top-5" is an arbitrary pick among equivalents and **ID-overlap recall measures document
identity, not relevance.** 72.5% is a lower bound on quality, not a measure of it — a corpus
with near-duplicates breaks this metric, and a judged relevance set is what would settle it.

**That judged set now exists: `semsearch-judgeset.md`** (built 2026-11-05, 40 queries, 1,085
judged pairs, four arms pooled at depth 10). Measured there: the embedding beats the best FTS
configuration by **+0.194 nDCG@10** (0.669 against 0.475), 95% CI [+0.024, +0.361], and a control
arm running the same FTS terms over the *same* text the embedding saw rules out the source text
as the explanation (+0.037, interval crosses zero). Read the magnitude with its caveat: 40
queries have 80% power only for an effect of 0.241, so the direction is sound and the point
estimate is probably inflated. `noise-retrieval-fts.md` has FTS at F1 0.475 on a different task with a different
ground truth; the two are **not comparable** and are not compared here.

## 5. The index silently returned ZERO rows for a filtered search

The worst finding here, and it only appeared because the endpoint was exercised after 0019
rather than before. Adding the HNSW index turned a working spatially-filtered search into no
results at all — not slower, **wrong** — and only at small LIMIT:

| LIMIT | default | `hnsw.iterative_scan=strict_order` |
|---|---|---|
| 1 | **0** | 1 |
| 3 | **0** | 3 |
| 5 | 5 | 5 |
| 10, 20, 30, 50 | correct | correct |

The plan says why:

    Nested Loop
      Join Filter: (e.identifier = hit.identifier)
      Rows Removed by Join Filter: 131680
      ->  Index Scan using bekendmaking_embedding_hnsw on bekendmaking_embedding e
      ->  Materialize   (hit, 3292 rows)

The planner drives the join from the **HNSW index as the outer loop** — walking global nearest
neighbours in distance order and testing each against the isochrone. An HNSW scan returns a
BOUNDED number of candidates (`ef_search`, 40 by default); when none of them fall inside the
filter it stops and the query ends with no rows instead of looking further. 131,680 rows
removed by the join filter and nothing left.

Three things make this the nastiest bug of the session:
- **It is a wrong answer, not a slow one.** An empty result and "nothing matches here" are
  indistinguishable to the caller — the failure this codebase refuses everywhere else.
- **It depends on LIMIT and on which plan the planner happens to pick.** At LIMIT 5 it is
  correct; at 3 it is empty. The same SQL on a plain connection returned rows while the pooled
  one did not, because the row estimates differed.
- **It did not exist before the index.** 0018's comment predicted the index "may simply never
  be chosen"; being chosen and being wrong was not on the list.

Fixed with pgvector's own mechanism: `SET LOCAL hnsw.iterative_scan = strict_order` on the
filtered branch, which keeps scanning until enough rows survive the filter. `strict_order`
rather than `relaxed_order` because the endpoint prints a similarity per row and sorts by it;
relaxed may emit rows slightly out of distance order and the numbers would then disagree with
the order they appear in. It is set per query, not pool-wide in `db.py`, because `hnsw.*` is
not a known parameter until the extension loads into the session — a SET at connection setup
fails outright. If it is ever unavailable the service logs a warning rather than proceeding
quietly, since under-returning is precisely what must not pass unnoticed.

## 6. One performance defect in my own query, found by EXPLAIN

The endpoint joined `bekendmaking_activity` alongside the ranking. That view reassembles
`activity_text` with six regexes per row, so it ran them over every candidate to build snippets
for the twenty rows that survive — the vector sort was 787 ms and the view was the rest.
Ranking now takes its LIMIT first and the expensive joins run on what is left.

## 7. Decisions, and what is left

- `vector(768)` lives in its own table (0018), not on `bekendmaking_noise_score`, which 0016
  keeps narrow on purpose. The row carries the model and the **sha1 of the text encoded**:
  `activity_text` comes from a VIEW and moves when bodies are fetched or patterns improve, so
  a vector can go stale with nothing revealing it. `noise_emb_cache.npz` is keyed on identifier
  alone and was therefore discarded rather than reused.
- The encoder is optional (`requirements-local.txt`) and `/search/notices` answers **503 with
  the reason**, never an empty 200 — an unavailable encoder and a query with no matches are
  different facts.
- `ef_search` is left at its default; `hnsw.iterative_scan=strict_order` is set on the
  filtered branch and is not optional — see §5.
- `ef_search` is left at its default. Raising it costs the index entirely unless the planner is
  forced, and the recall it buys is partly an artefact of the metric.

Open:
- **Equal-weight RRF fusion with FTS is worse than pure vector** (−0.063 nDCG, inside noise),
  although the two arms are strongly complementary per query. A weight or a query router is the
  open move; it needs a pool extension and another judging pass, not a re-score.
  `semsearch-judgeset.md` §Result.
- **Three query intents have no relevant document in a 4-arm pool**, one of them (a skip on the
  street) unreachable by three different retrieval methods now. Same file.
- Only one embedding model has ever been tried, across this whole line of work.
- The isochrone path ignores the HNSW index by design; if a mode ever searches a wide area
  without a spatial filter, that assumption needs re-measuring.
- Query encoding is 174–314 ms in-process, which is now the largest single component of a
  filtered search. Untouched — batching does not apply to one query, and a smaller model has
  not been tried.
