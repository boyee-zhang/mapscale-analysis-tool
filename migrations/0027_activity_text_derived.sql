-- `activity_text` stops being recomputed on every read.
--
-- 0015 chose a view and said why, and it also said how this would end:
--
--     "If the per-query regex cost ever matters it can be materialised then, on measurement
--      rather than in anticipation."
--
-- It matters, and here is the measurement (2026-11-14, 94,183 rows, falsifier verdict 14 on
-- ledger claim 17, raw output in docs/pg-notes/raw/notices-{ranking-explain,view-decomposition,
-- ranking-falsify}.txt):
--
--   * `/search/notices` ranking query: 1,778-2,040 ms warm over 24 runs. The HNSW scan inside
--     it is 0.76-1.13 ms. Essentially all of the rest is this view.
--   * The planner puts the view on the PROBE side of a hash built from the already-LIMITed
--     ranked rows, so all 94,183 view rows are projected to decorate 20. The same join with
--     no view expression evaluated is 9-12 ms, which is the control that names the projection
--     rather than the join or the I/O.
--   * Cost is per REFERENCED VIEW COLUMN, not per row read: `activity_text` alone is
--     5,137-5,198 ms serially, and adding `activity_src` to the same SELECT doubles it to
--     9,750-9,933 ms. 661,518 buffer hits is exactly 8x the 83,289 of one detoast pass, so
--     `body_text` is decompressed EIGHT times per row. Of the serial cost the six
--     `regexp_match` calls are ~61% and TOAST decompression ~29% -- so no cleverer single
--     regex can go below the detoast floor.
--   * Invariant over every axis varied: limit 1/5/20/50, a doc_type filter, a one-week window,
--     a Dutch query and the isochrone branch all cost 1,778-1,849 ms. A query returning 1 row
--     costs what one returning 50 does, which is the cleanest statement that this is not the
--     ranking.
--
-- 0015's two objections to a column are answered rather than ignored:
--
--   "a backfill" -- yes, once, and it is cheap: the whole projection is ~5 s of compute.
--   "a second copy of these patterns inside the importer, and the two would drift the first
--    time a pattern improved" -- the patterns are NOT copied. They stay in SQL, in
--    `bekendmaking_activity_derive` below, which is the same expression text 0015 shipped.
--    `scripts/derive_activity.py` only decides WHICH rows to run it for. There is one copy.
--
-- Why a derived table rather than STORED generated columns, which were also tested and do
-- work for the `regexp_match` half (`concat_ws` is STABLE, so the final concatenation cannot
-- be generated): a generated column cannot be stale, which is stronger, but it also has no
-- version, so improving a pattern means rewriting the whole table and nothing can say which
-- rows were computed under which rules. The decisive argument is downstream: `activity_text`'s
-- real consumer is `bekendmaking_embedding`, which is already a batch step carrying
-- `text_sha1` to detect exactly this. Today the text is FRESHER than the vector derived from
-- it and nothing says so; and that `text_sha1` check is itself unusable, because computing the
-- current text to compare against means running this view over everything. Storing the text
-- makes the check a cheap join and lets both derivations share one run. Instant freshness was
-- freshness nobody consumed.
--
-- Scale note, because this answer is scale-bound the way the `unnest` vs `= ANY` one was: at
-- 94,183 rows a full re-derive is seconds and generated columns would also have been fine. The
-- version column is what keeps this answer correct at 10-20M rows, which is the size this
-- repo's Postgres parameters are chosen to make observable.


-- The patterns, unchanged from 0015, with only the passthrough columns removed. This is the
-- ONLY place they exist. Reading it for a batch of identifiers is an index lookup per row;
-- reading it for the whole table is the ~5 s that used to happen per query.
CREATE VIEW bekendmaking_activity_derive AS
WITH f AS (
    SELECT b.identifier, b.title, bb.body_text,
           (regexp_match(bb.body_text, '(?:^|\n)Omschrijving\s*:\s*([^\n]+)'))[1]   AS omschrijving,
           (regexp_match(bb.body_text, '(?:^|\n)Toelichting\s*:\s*([^\n]+)'))[1]    AS toelichting,
           (regexp_match(bb.body_text, '(?:^|\n)Onderwerp\s*:\s*([^\n]+)'))[1]      AS onderwerp,
           (regexp_match(bb.body_text, '(?:^|\n)Activiteiten\s*:\s*([^\n]+)'))[1]   AS activiteiten,
           (regexp_match(bb.body_text, '(?:^|\n)Betreft\s*:\s*([^\n]+)'))[1]        AS betreft,
           (regexp_match(bb.body_text, '(?:^|\n)Naam evenement\s*:\s*([^\n]+)'))[1] AS naam_evenement,
           CASE WHEN bb.body_text ~ '^Stadsdeel\s*:' THEN
               btrim(regexp_replace(
                   split_part(split_part(bb.body_text, E'\n', 2), ',', 1),
                   '\s+(ter hoogte van|op het adres|op adres)\s.*$', ''))
           END AS apv_regel
    FROM bekendmaking b
    LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
)
SELECT f.identifier,
       array_remove(ARRAY[
           CASE WHEN f.apv_regel      IS NOT NULL THEN 'apv_regel'      END,
           CASE WHEN f.omschrijving   IS NOT NULL THEN 'omschrijving'   END,
           CASE WHEN f.toelichting    IS NOT NULL THEN 'toelichting'    END,
           CASE WHEN f.onderwerp      IS NOT NULL THEN 'onderwerp'      END,
           CASE WHEN f.naam_evenement IS NOT NULL THEN 'naam_evenement' END,
           CASE WHEN f.activiteiten   IS NOT NULL THEN 'activiteiten'   END,
           CASE WHEN f.betreft        IS NOT NULL THEN 'betreft'        END
       ], NULL) AS activity_src,
       coalesce(
           nullif(concat_ws(' | ', f.apv_regel, f.omschrijving, f.toelichting, f.onderwerp,
                                   f.naam_evenement, f.activiteiten, f.betreft), ''),
           nullif(btrim(concat_ws(' | ', f.title,
                                  nullif(btrim(split_part(f.body_text, E'\n', 1)), ''))), ''),
           ''
       ) AS activity_text
FROM f;

COMMENT ON VIEW bekendmaking_activity_derive IS
    'The extraction itself, for scripts/derive_activity.py to run per row. The only copy of '
    'these patterns. Never read this over the whole table from a request path.';


CREATE TABLE bekendmaking_activity_text (
    identifier    text PRIMARY KEY REFERENCES bekendmaking (identifier) ON DELETE CASCADE,
    activity_text text NOT NULL,
    activity_src  text[] NOT NULL,
    -- Bumped when a pattern in the view above changes. A row whose rule_version is behind is
    -- re-derivable without touching, or even reading, the rest of the table.
    rule_version  int NOT NULL,
    -- The bekendmaking_body run this text was derived from. The staleness test is two integer
    -- comparisons and needs no detoast: a digest of body_text would have to decompress the
    -- 72 MB TOAST relation to decide whether it had changed, which is the cost being removed.
    body_etl_run  bigint,
    derived_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT activity_src_not_null_elements CHECK (array_position(activity_src, NULL) IS NULL)
);

COMMENT ON TABLE bekendmaking_activity_text IS
    'activity_text, derived once per (body run, rule version) instead of on every read. '
    'Filled by scripts/derive_activity.py; read through the bekendmaking_activity view.';

CREATE INDEX activity_text_stale ON bekendmaking_activity_text (rule_version, body_etl_run);


-- Backfill inside the migration, deliberately. The alternative -- ship the empty table and
-- let scripts/derive_activity.py fill it afterwards -- leaves a window in which
-- `bekendmaking_activity` returns '' for every row and the FTS materialized view below is
-- empty, i.e. search silently returns nothing. The whole projection is ~5 s of compute at
-- 94,183 rows, which is cheaper than that window is dangerous. `derive_activity.py` is for
-- the incremental re-derives afterwards, not for this one.
INSERT INTO bekendmaking_activity_text
       (identifier, activity_text, activity_src, rule_version, body_etl_run)
SELECT d.identifier, d.activity_text, d.activity_src, 1, bb.etl_run
FROM bekendmaking_activity_derive d
LEFT JOIN bekendmaking_body bb ON bb.identifier = d.identifier;


-- The read surface keeps its exact column list, so service.py, embed_bekendmaking.py and
-- score_noise_embedding.py do not change. What changes is that nothing here evaluates a regex.
--
-- LEFT JOIN with coalesce to '' and not an inner join: an undelivered row must not silently
-- disappear from search results. '' is what 0015's own final coalesce already produced for a
-- row with no body and no title, so this preserves the contract rather than inventing a NULL.
-- `bekendmaking_activity_pending` below is how you see that state instead of guessing at it.
--
-- 0020's FTS materialized view selects from this view, so it has to come down with it and go
-- back up afterwards. Note what that dependency already says: the FTS path materialised this
-- text in 0020 and the semantic path did not, so half the system had already stopped paying
-- the per-read cost.
DROP MATERIALIZED VIEW bekendmaking_search_text;
DROP VIEW bekendmaking_activity;

CREATE VIEW bekendmaking_activity AS
SELECT b.identifier, b.gemeente, b.doc_type, b.published_at,
       bb.visitors_peak, bb.status AS body_status,
       coalesce(t.activity_src, ARRAY[]::text[]) AS activity_src,
       coalesce(t.activity_text, '')             AS activity_text
FROM bekendmaking b
LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
LEFT JOIN bekendmaking_activity_text t ON t.identifier = b.identifier;

COMMENT ON VIEW bekendmaking_activity IS
    'What is actually being done, per publication. A lookup since 0027 -- the extraction runs '
    'in scripts/derive_activity.py, not here. Same columns as 0015.';


-- Rebuilt exactly as 0020 defined it, with data: the table above is already filled, so there
-- is no moment at which this is empty.
CREATE MATERIALIZED VIEW bekendmaking_search_text AS
SELECT identifier, activity_text
FROM bekendmaking_activity a
WHERE activity_text <> ''::text;

CREATE UNIQUE INDEX bekendmaking_search_text_pk ON bekendmaking_search_text (identifier);
CREATE INDEX bekendmaking_search_text_fts
    ON bekendmaking_search_text USING gin (to_tsvector('dutch'::regconfig, activity_text));


-- Rows whose text is missing or was derived under an older rule version or an older body run.
-- Empty is the normal state; anything in it is work scripts/derive_activity.py has not done.
CREATE VIEW bekendmaking_activity_pending AS
SELECT b.identifier,
       CASE WHEN t.identifier IS NULL          THEN 'never_derived'
            WHEN t.body_etl_run IS DISTINCT FROM bb.etl_run THEN 'body_changed'
            ELSE 'rule_changed' END AS why,
       t.rule_version, t.body_etl_run, bb.etl_run AS current_body_run
FROM bekendmaking b
LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
LEFT JOIN bekendmaking_activity_text t ON t.identifier = b.identifier
WHERE t.identifier IS NULL
   OR t.body_etl_run IS DISTINCT FROM bb.etl_run
   OR t.rule_version <> 1;
