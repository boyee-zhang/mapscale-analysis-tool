-- claim.status stops being a column.
--
-- 0021 made it one, with DEFAULT 'open' and a CHECK over four values, and then nothing ever
-- wrote it. The three rows backfilled by hand all said 'survived'. Two of them have a latest
-- verdict of DESTROYED:
--
--   1 | status=survived | latest_verdict=DESTROYED
--   2 | status=survived | latest_verdict=SURVIVED
--   3 | status=survived | latest_verdict=DESTROYED
--
-- And claim_stale() filtered on `status IN ('open','survived')`, so both refuted claims were
-- still being checked as live. A ledger built to stop a wrong conclusion from looking right
-- was itself a wrong conclusion that looked right, in two rows out of three.
--
-- The fix is not to correct the rows. A column that can be written wrongly will be written
-- wrongly again, and this one has a 100% error rate over its entire history. Deriving it in a
-- trigger would be weaker than it looks: a trigger can be disabled, and the column stays,
-- readable by anything that does not know it is maintained. Dropping it leaves nothing to get
-- wrong -- the same reasoning as this repo's `upstream/` boundary, where the rule is enforced
-- by there being no other place to put the code.
--
-- REFUSED maps to 'open', not to its own state: the falsifier refuses when its inputs are
-- missing, which means nothing was tested, which means the claim is exactly as open as before.

DROP FUNCTION claim_stale(jsonb);   -- superseded by verdict_stale() in 0023; see below

DROP INDEX claim_live;
ALTER TABLE claim DROP CONSTRAINT claim_status_check;
ALTER TABLE claim DROP CONSTRAINT claim_superseded_iff_pointed;
ALTER TABLE claim DROP COLUMN status;

-- `claim_live` was a partial index on the dropped column. The lookup it served is "recent
-- claims"; liveness now comes from the view below, which is small enough to scan.
CREATE INDEX claim_recent ON claim (made_at DESC);

CREATE VIEW claim_current AS
SELECT c.id, c.topic, c.text, c.made_by, c.made_at, c.context, c.scope, c.superseded_by,
       lv.verdict AS latest_verdict,
       lv.scope   AS latest_scope,
       lv.at      AS latest_verdict_at,
       CASE
           WHEN c.superseded_by IS NOT NULL THEN 'superseded'
           WHEN lv.verdict = 'DESTROYED'    THEN 'destroyed'
           WHEN lv.verdict = 'SURVIVED'     THEN 'survived'
           ELSE 'open'
       END AS status
FROM claim c
LEFT JOIN LATERAL (
    SELECT verdict, scope, at FROM verdict WHERE claim_id = c.id ORDER BY id DESC LIMIT 1
) lv ON true;


-- Why claim_stale(jsonb) is dropped rather than kept alongside verdict_stale().
--
-- Two functions answering "is this still true" is two answers, and the one that is wrong
-- would stay documented and callable. claim_stale needed its caller to assemble the current
-- world by hand; it had 0 callers in 12 days and the one stale number it existed to catch was
-- found by hand anyway. 0022 -- its whole migration -- exists only to handle a jsonb null
-- that a content digest cannot produce.
--
-- What is lost is real and worth stating: key-level precision. claim_stale could say "only
-- corpus_size moved"; verdict_stale says "the corpus facet moved". The claim's own
-- measurement parameters stay in claim.context, which keeps its gin index and is still the
-- right place for them -- what leaves is the world's dimensions, which were mixed in with
-- them. Claim #2's context held `corpus_size` (the world) next to `method` (the experiment);
-- that conflation is exactly why claim_stale had to guess with `now_ctx ? k`.
