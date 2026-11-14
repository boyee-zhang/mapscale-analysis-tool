-- 0031's pending view read `bekendmaking` twice, and the plan said so.
--
-- It selected `FROM bekendmaking b JOIN bekendmaking_activity_source s` -- but the source view
-- is itself `bekendmaking LEFT JOIN bekendmaking_body`, so the base table was scanned once for
-- the driver and once inside the view, then hash-joined to itself:
--
--     Seq Scan on bekendmaking b_1        <- inside the source view
--     Seq Scan on bekendmaking_body bb    <- inside the source view
--     Seq Scan on bekendmaking b          <- the redundant driver
--     Seq Scan on bekendmaking_activity_text t
--     Execution Time: 489-504 ms, 139,640 buffers
--
-- The source view already has exactly one row per publication (`identifier` is the primary key
-- of both tables and the join is a LEFT JOIN from `bekendmaking`), so driving from it is not an
-- optimisation that trades anything away -- the row set is identical by construction.
--
--     Seq Scan on bekendmaking b, bekendmaking_body bb, bekendmaking_activity_text t
--     Execution Time: 407-413 ms, 125,624 buffers
--
-- `current_body_run` leaves the output with it: reporting it required a third join to
-- `bekendmaking_body` purely to print a number no longer in the staleness test.
-- `t.body_etl_run` stays, which is the provenance that was actually being read.
--
-- MEASURED AND REJECTED, so the next person does not re-measure it: covering indexes on
-- `bekendmaking (identifier, title_fp)` and `bekendmaking_body (identifier, body_text_fp)` do
-- work -- after a VACUUM sets the visibility map, both sequential scans become index-only and
-- buffer READS fall 15,933 -> 3,350. But wall time only goes 413 -> 328 ms, because the
-- remaining cost is CPU: 94,183 md5 computations plus a hash join that spills to temp at
-- `work_mem=4MB`. 14 MB of index for 85 ms on a check that runs once per import run is not
-- worth it, and the indexes do not touch the term that dominates. Re-measure before adding
-- them, not because this note is old but because the balance is what would have to change.
--
-- (The first attempt at that measurement was wrong in a way worth recording: it ran
-- immediately after 0031's `ALTER TABLE … ADD COLUMN … GENERATED … STORED`, which rewrites the
-- table and leaves `relallvisible = 0`. No index-only scan is possible in that state, so the
-- planner kept the sequential scans and the indexes measured as a 4% LOSS. It was measuring
-- the vacuum state, not the indexes.)

DROP VIEW bekendmaking_activity_pending;
CREATE VIEW bekendmaking_activity_pending AS
SELECT s.identifier,
       CASE WHEN t.identifier IS NULL                       THEN 'never_derived'
            WHEN t.source_fp IS NULL                        THEN 'fingerprint_unknown'
            WHEN t.source_fp IS DISTINCT FROM s.source_fp   THEN 'source_changed'
            ELSE 'rule_changed' END AS why,
       t.rule_version,
       t.source_fp,
       s.source_fp AS current_source_fp,
       t.body_etl_run
FROM bekendmaking_activity_source s
LEFT JOIN bekendmaking_activity_text t ON t.identifier = s.identifier
WHERE t.identifier IS NULL
   OR t.source_fp IS DISTINCT FROM s.source_fp
   OR t.rule_version IS DISTINCT FROM activity_rule_current();

COMMENT ON VIEW bekendmaking_activity_pending IS
    'Publications whose activity_text is missing, was derived from inputs that have since '
    'changed, or was derived under a rule version that is no longer current. Empty is the '
    'normal state. The staleness test is a fingerprint over the declared input set '
    '(bekendmaking_activity_source), not a list of watched fields -- so adding an input to the '
    'extraction cannot silently go unwatched.';
