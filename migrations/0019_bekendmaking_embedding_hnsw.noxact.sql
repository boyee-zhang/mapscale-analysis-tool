-- An HNSW index over the document vectors, for the corpus-wide case only.
--
-- 0018 deliberately left this out so its cost and its effect would both be numbers. They now
-- are, MEASURED on 94,183 vectors (389 MB) against shared_buffers=128MB, first run discarded:
--
--   corpus-wide exact scan      235-300 ms warm,  7,186 ms cold
--   inside a 10-min isochrone      39-42 ms warm,     72 ms cold   (4,221 candidates)
--
-- So the product's main query does NOT need this. The spatial predicate runs first and leaves
-- a few thousand rows; an exact scan over those is 40 ms, and an ANN index cannot help a join
-- that has already been narrowed -- pgvector would have to walk the graph and discard almost
-- everything it found, or fall back to the scan it was meant to replace.
--
-- What it is for is the corpus-wide query, where there is no filter to prune with. The number
-- that makes that worth indexing is not the 240 ms warm figure but the 7,186 ms COLD one: at
-- 3x the buffer pool, a sequential pass over this table is mostly disk, and the first query
-- after a restart pays all of it. The project's own note says to discard the first run when
-- measuring -- it does not say the user gets to.
--
-- `.noxact` because CREATE INDEX CONCURRENTLY cannot run inside a transaction. Concurrently,
-- because an HNSW build on this table takes long enough that locking out writes would block
-- the next re-embedding run.
--
-- vector_cosine_ops, not l2 or ip: e5 vectors are L2-normalised, so all three rank identically,
-- and cosine is the one whose operator (`<=>`) matches what the service asks for. An index
-- built for a different operator is simply never used, silently.

CREATE INDEX CONCURRENTLY bekendmaking_embedding_hnsw
    ON bekendmaking_embedding USING hnsw (embedding vector_cosine_ops);
