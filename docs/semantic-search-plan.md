# Semantic search over the publication corpus — the brief

Written on `experiment/semantic-search-ab` for the `feature/semantic-search` branch, which is
cut from the same commit. Read this before starting; it is the handover, not a design doc.

## The goal

A user types free text — `pile driving at night near me`, `markets this summer` — and gets
publications ranked by semantic similarity, clipped to an isochrone.

## Why this is the next step, and what it retires

The embedding line of work has already produced one standing caveat that this is meant to
remove: **the six query clauses used in every experiment so far were written by hand, by me.**
Letting the user supply the query removes that, and removes it by construction rather than by
argument.

It also acts on the sharpest measured result in the project. From
`docs/pg-notes/noise-serving.md`: the deployed noise classifier's BINARY verdict reproduces a
four-value `doc_type IN (...)` lookup on 99.61% of the corpus with identical F1 — but WITHIN a
doc_type, where that lookup is constant, the embedding separates hand-labelled noisy from quiet
at AUC 0.911–1.000. **The embedding's value is ranking, not classification.** Search is the
product shape that uses ranking directly.

## Read first

| file | what it will stop you repeating |
|---|---|
| `docs/pg-notes/noise-retrieval-fts.md` | every hand-written keyword query scored BELOW the retrieve-everything floor (F1 0.621). Dutch compounding: a tsquery lexeme cannot reach inside `evenementenvergunning`; `:*` fixes it and is 76x faster. |
| `docs/pg-notes/noise-embedding.md` | the gain came from DECOMPOSING the concept into clauses and max-pooling (AUC 0.689 for one sentence vs 0.862 for six), not from the model. `max` is the OR operator over clauses; `mean` dilutes. |
| `docs/pg-notes/noise-serving.md` | the doc_type-lookup finding above, and why the score is precomputed. |
| `migrations/0016_bekendmaking_noise_score.sql` | the reasoning for why vectors were NOT stored, and what changes now. |

## Shape, to be confirmed by measurement rather than assumed

1. **pgvector.** Install the extension in the local `imresamu/postgis:16-3.4`, add a
   `vector(768)` column and an HNSW index in a numbered migration. 190 MB of vectors against a
   deliberately small `shared_buffers=128MB` is the regime where HNSW behaviour is *visible*
   rather than hidden by cache — that is a feature here, not a problem to tune away.
2. **Backfill offline, reusing the cache.** `etl/derive/noise_score.py` already embeds
   the corpus into `data/noise_emb_cache.npz` (gitignored, 185 MB, `ids` + a `(61853, 768)`
   float32 array). Extend it for the rows the national import added; do not re-embed from
   scratch. Throwaway venv:
   `python3 -m venv /tmp/embvenv && /tmp/embvenv/bin/pip install sentence-transformers==3.4.1 scikit-learn==1.6.1 asyncpg python-dotenv`
3. **Embed the query at request time.** One short forward pass, which is a different cost from
   embedding ~3,000 matched documents per request — that asymmetry is the whole reason document
   vectors are precomputed and query vectors are not. Decide where the model lives (beside the
   API, or a small separate service) and say why.
   **`"query: "` for queries and `"passage: "` for documents.** e5 requires both; getting it
   wrong degrades everything silently, with no error anywhere.
4. **Endpoint.** `/search/notices`, or an extension of `/bekendmakingen`, combining vector
   similarity with the existing PostGIS isochrone clipping. Include
   `EXPLAIN (ANALYZE, BUFFERS)` showing which index each stage actually uses.

## Done looks like

- migration + backfill script + working endpoint + a frontend entry point
- a measured comparison against the FTS baseline, on a hand-labelled query set, with folds
  that cannot leak — written up as `docs/pg-notes/semantic-search.md`, raw output under
  `docs/pg-notes/raw/`
- `ruff check api/` clean, `.venv/bin/python -m pytest tests/unit` green, `npm run build` green
- numbers quoted with the population they were measured on, and an explicit list of what was
  NOT measured

## Two traps already paid for here

- **`db.py` sets `plan_cache_mode=force_custom_plan` and it is load-bearing.** Spatial queries
  pass the search shape as a parameter, so a generic plan has no bounding box to estimate from:
  the same query measured 2.4–3.1 s for five executions and **104 s on the sixth**.
- **`ST_DWithin(geom::geography, …)` is unindexable** against a geometry GiST. Use
  `ST_Buffer(pt::geography, r)::geometry` with `ST_Contains` — measured 1445 → 52 buffers.

## House rules this project holds itself to

Folds cut so templates or municipalities cannot leak across them; a held-out fold measured
ONCE and spent afterwards; every number quoted with the population behind it; HTTP 200 is not
evidence; an absent answer and a negative answer never share a branch. Any issue found while
working gets surfaced with an explicit fix-or-defer decision — deferring is fine, dropping
silently is not.
