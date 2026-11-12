-- Dutch full-text search over publications: the BASELINE semantic search is measured against.
--
-- WHY THIS IS A MIGRATION AND NOT A HAND-MADE INDEX. It was one. `bekendmaking_fts_tmp` was
-- created by hand for the 2026-11-01 A/B experiment and recorded in docs/pg-notes/semsearch-ab.md
-- as an open problem: "in the local database and in no migration, which is the worst of both".
-- Every later comparison needs it, so it is promoted here and the hand-made one dropped. An
-- index a measurement depends on is part of the schema.
--
-- TWO INDEXES, BECAUSE THE TEXT IS THE AXIS MOST LIKELY TO BE CONFUSED WITH THE RANKING.
-- The embedding in `bekendmaking_embedding` was computed over `bekendmaking_activity.
-- activity_text` -- the applicant's own description, reassembled out of `body_text`. The
-- original FTS index was over `title || abstract`. Those are DIFFERENT TEXT: `abstract` is NULL
-- on 20,642 of 94,183 rows (22%) while `activity_text` is non-empty on all of them. Comparing
-- FTS-over-title+abstract against vectors-over-activity_text therefore varies two things at
-- once, and a win could be read as the ranking function when it was the source text. So both
-- are indexed and both are measured: `bekendmaking_search_text` holds exactly the text that was
-- embedded, which makes FTS-vs-vector a single-axis comparison, and the title+abstract index
-- stays as the second arm that prices the text axis on its own.
--
-- A MATERIALIZED VIEW, BECAUSE `bekendmaking_activity` IS A VIEW and an expression index cannot
-- be built on one. It carries the same staleness property the embeddings do -- `activity_text`
-- is six regexes over `body_text`, so it moves when a body is fetched or a pattern improves --
-- and the same remedy applies: REFRESH it in the same breath as re-running
-- scripts/embed_bekendmaking.py, or the two arms stop reading the same corpus and the
-- comparison quietly stops being single-axis. CONCURRENTLY needs the unique index below.

DROP INDEX IF EXISTS bekendmaking_fts_tmp;

CREATE INDEX IF NOT EXISTS bekendmaking_fts
    ON bekendmaking
    USING gin (to_tsvector('dutch', coalesce(title, '') || ' ' || coalesce(abstract, '')));

CREATE MATERIALIZED VIEW IF NOT EXISTS bekendmaking_search_text AS
SELECT a.identifier, a.activity_text
FROM bekendmaking_activity a
WHERE a.activity_text <> '';

CREATE UNIQUE INDEX IF NOT EXISTS bekendmaking_search_text_pk
    ON bekendmaking_search_text (identifier);

CREATE INDEX IF NOT EXISTS bekendmaking_search_text_fts
    ON bekendmaking_search_text
    USING gin (to_tsvector('dutch', activity_text));
