-- Two declared facet paths moved with the scripts/ -> etl/ refactor. This is the forward-only
-- way to change data a migration seeded.
--
-- 0025 and 0028 INSERT `world_facet.paths`, and `etl/ledger/claims.py probe_paths` hashes
-- ROOT / <each path> at probe time. A path that no longer exists is recorded as 'ABSENT'
-- rather than skipped (0025's rule, and the right one: dropping it would make a facet whose
-- inputs were DELETED compare equal to one where they are intact). So without this migration
-- both facets would keep reporting, correctly but uselessly, that their inputs vanished.
--
-- Editing 0025/0028 in place is what this does NOT do: migrations are forward-only, the guard
-- hook denies writing an applied one, and a database that applied the old text would be in a
-- state no file describes.
--
-- WHAT THIS COSTS, stated rather than discovered later: both digests move once, so
-- `claims.py stale` will report **verdict 13** (judgeset) and **verdict 15** (activity_derive)
-- as untested. That is the correct reading -- the bytes those verdicts were reached over are
-- at different paths now, and the content digest of a path set is defined over the paths.
-- It is the cost 0025 already records for path facets, paid deliberately.
--
-- WHAT THIS DOES NOT FIX. `activity_derive` still cannot see the derivation it is named after:
-- it lists 0015 and 0027 (applied migrations, whose digests can never move), the search
-- service, and the derive CLI -- but not `etl/derive/activity_rules.py`, which holds the derive
-- SQL, the PENDING SQL and the extractor registries, nor the importer, nor 0029/0030/0031/0032.
-- That is a recorded Known Issue with an unassigned owner, and widening the facet is a
-- NARROWING assertion about what a measurement reads, so it is a decision of its own and not
-- something a rename should slip in.

UPDATE world_facet
SET paths = array_replace(paths, 'scripts/derive_activity.py', 'etl/derive/activity.py')
WHERE name = 'activity_derive';

UPDATE world_facet
SET paths = array_replace(paths, 'scripts/experiments/judgeset_score.py',
                                 'etl/experiments/judgeset_score.py')
WHERE name = 'judgeset';

-- Self-checking, because an UPDATE that matched nothing looks exactly like one that did the
-- job. Both halves matter: nothing may still point into scripts/, and every declared path must
-- name a file that exists -- which is the property the digest is supposed to be about.
DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(f.name || ':' || p, ', ')
      INTO bad
      FROM world_facet f, unnest(f.paths) AS p
     WHERE p LIKE 'scripts/%';
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'world_facet.paths still names the old scripts/ layout: %', bad;
    END IF;

    IF EXISTS (SELECT 1 FROM world_facet WHERE name = 'activity_derive'
                AND NOT paths @> ARRAY['etl/derive/activity.py']) THEN
        RAISE EXCEPTION 'activity_derive did not pick up etl/derive/activity.py';
    END IF;

    IF EXISTS (SELECT 1 FROM world_facet WHERE name = 'judgeset'
                AND NOT paths @> ARRAY['etl/experiments/judgeset_score.py']) THEN
        RAISE EXCEPTION 'judgeset did not pick up etl/experiments/judgeset_score.py';
    END IF;
END $$;
