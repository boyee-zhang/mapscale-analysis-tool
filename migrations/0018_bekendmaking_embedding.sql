-- Document vectors, so a free-text query can be answered by meaning rather than by wording.
--
-- WHY A TABLE OF ITS OWN, and not a column on `bekendmaking_noise_score`. That table answers
-- one model's question and is kept deliberately narrow so it stays in cache (0016 says so).
-- An embedding is not that model's property: the noise score is DERIVED from it, and so is
-- search, and so is anything else that ever needs similarity. Hanging it off the score table
-- would make a general asset look like a detail of one classifier, and would tie its lifetime
-- to that classifier's retraining.
--
-- WHY THE VECTORS ARE STORED AT ALL, having been refused in 0016. That refusal was correct
-- for what existed then: nothing read them, the request path needed only the score, and 190 MB
-- against a 128 MB shared_buffers would have evicted everything else to serve a column with no
-- reader. Search is the reader. The cost has not changed; the benefit has.
--
-- WHY THE SIMILARITY IS COMPUTED HERE AND NOT IN PYTHON. This is the decisive reason for the
-- extension, more than any index: an isochrone over a year matches a few thousand
-- publications, and shipping their vectors to the application would be a few thousand times
-- 3 KB -- megabytes per request, to compute a dot product. `<=>` keeps it where the data is.
--
-- WHY NO INDEX IN THIS MIGRATION. An HNSW build is far cheaper as a bulk operation on a loaded
-- table than incrementally through 94,183 inserts, and -- the honest reason -- it is not yet
-- established that this workload can use one. The product query filters spatially FIRST, which
-- already cuts the corpus to a few thousand rows; an ANN index over the whole table may simply
-- never be chosen by the planner. 0019 adds it as a separate, measurable step, so the index's
-- build cost and its effect are both numbers rather than assumptions.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE bekendmaking_embedding (
    identifier text PRIMARY KEY REFERENCES bekendmaking (identifier) ON DELETE CASCADE,

    -- Which model produced it. A vector is meaningless without this, and two models' vectors
    -- must never be compared -- the arithmetic succeeds and the answer is nonsense, which is
    -- the worst shape of error. Stored per row rather than assumed globally so a re-embedding
    -- can proceed in batches without a window where the table holds a silent mixture.
    model      text NOT NULL,

    -- 768 is multilingual-e5-base. The dimension is part of the type, so a model with a
    -- different width cannot be written here by accident; it needs a migration, which is the
    -- correct amount of friction.
    embedding  vector(768) NOT NULL,

    -- What was actually encoded. `bekendmaking_activity.activity_text` is a VIEW over fields
    -- extracted from the body, so it changes when those patterns improve -- and a vector
    -- computed from an older version of the text is stale in a way nothing else would reveal.
    -- The hash makes that detectable: a backfill can re-embed exactly the rows whose source
    -- text moved, instead of re-embedding everything or trusting that nothing did.
    text_sha1  text NOT NULL,
    embedded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX bekendmaking_embedding_model ON bekendmaking_embedding (model);

COMMENT ON TABLE bekendmaking_embedding IS
    'Document vectors for semantic search. One row per publication per current model; '
    'text_sha1 is the sha1 of the exact text encoded, so staleness is detectable.';
