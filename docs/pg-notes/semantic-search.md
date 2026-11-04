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

That judged set does not exist yet, so no claim is made about retrieval quality against the
FTS baseline. `noise-retrieval-fts.md` has FTS at F1 0.475 on a different task with a different
ground truth; the two are **not comparable** and are not compared here.

## 5. One performance defect in my own query, found by EXPLAIN

The endpoint joined `bekendmaking_activity` alongside the ranking. That view reassembles
`activity_text` with six regexes per row, so it ran them over every candidate to build snippets
for the twenty rows that survive — the vector sort was 787 ms and the view was the rest.
Ranking now takes its LIMIT first and the expensive joins run on what is left.

## 6. Decisions, and what is left

- `vector(768)` lives in its own table (0018), not on `bekendmaking_noise_score`, which 0016
  keeps narrow on purpose. The row carries the model and the **sha1 of the text encoded**:
  `activity_text` comes from a VIEW and moves when bodies are fetched or patterns improve, so
  a vector can go stale with nothing revealing it. `noise_emb_cache.npz` is keyed on identifier
  alone and was therefore discarded rather than reused.
- The encoder is optional (`requirements-local.txt`) and `/search/notices` answers **503 with
  the reason**, never an empty 200 — an unavailable encoder and a query with no matches are
  different facts.
- `ef_search` is left at its default. Raising it costs the index entirely unless the planner is
  forced, and the recall it buys is partly an artefact of the metric.

Open:
- **No judged relevance set**, so retrieval quality is unmeasured. This is the gap that
  matters most and the only honest way to compare against FTS.
- Only one embedding model has ever been tried, across this whole line of work.
- The isochrone path ignores the HNSW index by design; if a mode ever searches a wide area
  without a spatial filter, that assumption needs re-measuring.
- Query encoding is 174–314 ms in-process, which is now the largest single component of a
  filtered search. Untouched — batching does not apply to one query, and a smaller model has
  not been tried.
