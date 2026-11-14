-- 0025's `world_facet_paths_nonempty` did not reject an empty path set.
--
-- Found by the must-reject half of scripts/verify_ledger.py on the first run after 0025, which
-- is the half that exists for exactly this. The arithmetic:
--
--     array_length(ARRAY[]::text[], 1)  =  NULL      -- not 0
--     NULL >= 1                         =  NULL
--     a CHECK constraint PASSES on NULL
--
-- So `CHECK (paths IS NULL OR array_length(paths, 1) >= 1)` accepted `ARRAY[]::text[]`, and an
-- empty path facet is the worst possible row in this schema: probe_paths([]) returns a constant,
-- so its digest never moves, so a verdict anchored to it NEVER REPORTS STALE. A facet whose
-- whole job is to expire claims, silently unable to. The same shape as the empty-200 /metrics
-- and the guard whose path rules all passed because of an undefined name -- the check survives,
-- the protection it was supposed to buy does not.
--
-- Postgres's three-valued logic is the mechanism, and it is worth stating because this repo now
-- has two bugs from it: this one, and 0022's `jsonb null IS DISTINCT FROM` false positive. A
-- CHECK is satisfied unless it evaluates to FALSE, so any sub-expression that can go NULL is a
-- hole in the constraint, not a rejection.

UPDATE world_facet SET paths = NULL WHERE paths IS NOT NULL AND array_length(paths, 1) IS NULL;

ALTER TABLE world_facet DROP CONSTRAINT world_facet_paths_nonempty;

ALTER TABLE world_facet ADD CONSTRAINT world_facet_paths_nonempty
    CHECK (paths IS NULL OR coalesce(array_length(paths, 1), 0) >= 1);
