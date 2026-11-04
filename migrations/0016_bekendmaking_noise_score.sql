-- The measured noise classifier, as stored scores rather than as a request-time model.
--
-- WHY THE SCORE IS PRECOMPUTED. The winning design measured in docs/pg-notes/noise-embedding.md
-- is logistic regression over 18 metadata features concatenated with a 768-d
-- `intfloat/multilingual-e5-base` embedding of `bekendmaking_activity.activity_text`
-- (F1 0.891 against 0.845 for the hand-written rule table in 0012). Producing that embedding
-- needs torch + sentence-transformers, about 1.1 GB installed. This repo deploys as a single
-- Mangum-wrapped Vercel function, so that dependency cannot enter the request path at all --
-- not as a slow path, not behind a flag. The model therefore runs offline in
-- `scripts/score_noise_embedding.py` against its own throwaway venv, and the request path
-- joins one `real` per publication.
--
-- WHY NOT STORE THE EMBEDDINGS TOO. 768 float4 per row over 61,853 rows is ~190 MB, against
-- ~1.5 MB for the scores. This container runs shared_buffers=128MB on purpose, so the vectors
-- would evict everything else to serve a column no query reads: the request path needs the
-- score, and retraining re-embeds from `activity_text`, which is already stored. pgvector is
-- not installed and is not needed for a dot product that happens offline. If an ANN search over
-- publications is ever wanted, that is when to add the extension and the column.
--
-- WHY THE THRESHOLD IS NOT STORED PER ROW. A cosine-style score needs a cutoff, and the cutoff
-- is a product decision that will be revisited; `is_noisy` as a stored boolean would make
-- revisiting it a 61,853-row backfill, and a stale copy of a classification is the exact
-- failure the 0011 view comment refuses. It lives in one `noise_model` row and the view below
-- applies it, so moving the cutoff is an UPDATE of one field.

CREATE TABLE noise_model (
    version     text PRIMARY KEY,
    trained_at  timestamptz NOT NULL DEFAULT now(),
    -- The sentence-transformers id, recorded because the score is meaningless without it and
    -- because only one model has ever been tried -- see the open item in the notes.
    embed_model text NOT NULL,
    -- Where the cutoff came from, so a future reader can tell a measured threshold from a
    -- number somebody liked: 'loblo_max_f1' is the leave-one-block-out argmax, 'precision_*'
    -- is the lowest threshold whose cross-validated precision cleared that bar.
    threshold   real NOT NULL CHECK (threshold > 0 AND threshold < 1),
    threshold_basis text NOT NULL,
    n_train     integer NOT NULL,
    -- Cross-validated precision/recall/F1 at `threshold`, plus the per-block spread. Kept
    -- beside the threshold because a score's scale of validity is part of the score: these
    -- were measured on 695 hand-labelled rows in three Amsterdam/Utrecht blocks, and the
    -- table it scores is 61,853 rows. A consumer that wants to know how much to trust a
    -- badge reads this row.
    metrics     jsonb NOT NULL,
    note        text
);

CREATE TABLE bekendmaking_noise_score (
    identifier    text PRIMARY KEY REFERENCES bekendmaking (identifier) ON DELETE CASCADE,
    model_version text NOT NULL REFERENCES noise_model (version),
    -- P(noisy). A probability, not a cosine: the logistic layer is what makes thresholds
    -- comparable across retrainings of the same feature set.
    score         real NOT NULL CHECK (score >= 0 AND score <= 1),
    scored_at     timestamptz NOT NULL
);

-- No index on (score) or (model_version). The only reader narrows spatially first -- an
-- isochrone over a year is hundreds to a few thousand publications -- and then joins on the
-- primary key, so a score index would never be chosen and would only cost writes on a rescore.
-- Add one when a query that scans by score exists, not before.

-- The cutoff applied in one place, as a view over the score table alone.
--
-- It deliberately does NOT join `bekendmaking`. The one reader already has that table in its
-- FROM list, and a view that re-joined it would put a second scan of 61,853 rows into every
-- request plan for no new column. So this is LEFT JOINed from the caller, which is also what
-- makes the NULL below arrive for free.
--
-- `is_noisy` is NULL exactly where no score exists -- a publication imported after the last
-- scoring run. NULL must reach the API as "not scored", never as "quiet": that is the same
-- distinction this endpoint already draws between `shape: "unavailable"` and `total: 0`, where
-- an absent source and a negative answer look identical to a caller and only one of them is
-- true. A LEFT JOIN from the caller yields NULL for both `score` and `is_noisy` together, so
-- the two cannot disagree.
CREATE VIEW bekendmaking_noise_rank AS
SELECT s.identifier,
       s.score,
       s.model_version,
       s.scored_at,
       m.threshold,
       s.score >= m.threshold AS is_noisy
FROM bekendmaking_noise_score s
JOIN noise_model m ON m.version = s.model_version;
