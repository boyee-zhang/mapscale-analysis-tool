-- Two things 0023 got wrong, both found by using it once.
--
-- 1. `code` was a false-staleness generator, and it was the facet this repo's own measurement
--    was anchored to. `probe_code` hashed `dirty_files`, a COUNT, so the digest moved three
--    times in one session (5 -> 7 -> 9 dirty files) while the measurement's frozen input stayed
--    byte-identical. It then produced the clearest possible demonstration: correcting the notes
--    exactly as verdict 12's own SCOPE instructed is what expired verdict 12.
--
--        verdict 12  claim 4  DESTROYED
--          code: cb457d4dbfe1 -> 2b145620ca71     # judgeset-scores.json sha256 unchanged
--
--    A count is the weakest identity available: seven dirty files could be any seven, so it
--    both moves on edits that do not matter and fails to distinguish states that do. `branch`
--    was in the payload for no reason at all -- the same commit under two branch names is the
--    same code, so a checkout -b expired every verdict.
--
-- 2. More fundamentally, `code` was the wrong SHAPE of facet for almost everything. A
--    measurement does not depend on "the repository"; it depends on specific bytes. The
--    judgeset scorer reads three frozen JSON files and two scripts. Anchoring it to the whole
--    repo makes every unrelated commit expire it, which is noise, and noise in a staleness
--    report is fatal in the way 0022 already recorded: the report gets skimmed, and the one
--    real entry gets skimmed with it.
--
-- So a facet may now be defined by a PATH SET instead of by a probe in code. It stays a facet
-- in every other respect -- global, shared, content-addressed, one row per distinct world --
-- which is what keeps the model from sprouting a second concept. Declaring one is an INSERT,
-- the same way widening the POI import is an INSERT into poi_category_rule.

ALTER TABLE world_facet ADD COLUMN paths text[];

ALTER TABLE world_facet ADD CONSTRAINT world_facet_paths_nonempty
    CHECK (paths IS NULL OR array_length(paths, 1) >= 1);

-- Which facets `claims.py verdict` anchors to when told nothing.
--
-- The rule lives in the table rather than in the script so that adding a facet cannot silently
-- change what every future verdict anchors to. `corpus` and `graph` are the shared world and
-- stay on by default: forgetting them fails toward over-reporting, which is visible, where
-- omitting them fails toward silence. `code` and every path facet are off, because anchoring to
-- them is a NARROWING assertion -- "this measurement reads only these bytes" -- and a narrowing
-- assertion has to be made on purpose.
ALTER TABLE world_facet ADD COLUMN default_anchor boolean NOT NULL DEFAULT true;

UPDATE world_facet
   SET default_anchor = false,
       probe = 'git rev-parse HEAD + HEAD^{tree} + sha256 of `git diff HEAD` when dirty'
 WHERE name = 'code';

-- The frozen inputs of the bekendmaking judgeset measurement: what each arm returned, the
-- grades, the blind sheet, and the two scripts that turn those into numbers. The scripts belong
-- in here -- change a metric definition and the result changes, so the measurement depends on
-- them exactly as much as on the data. `judgeset-scores.json` is deliberately absent: it is
-- derived from these, and its digest is already carried by the evidence rows.
INSERT INTO world_facet (name, probe, paths, default_anchor) VALUES (
    'judgeset',
    'sha256 of each path below',
    ARRAY['docs/pg-notes/judgeset-raw.json',
          'docs/pg-notes/judgeset-judgments.json',
          'docs/pg-notes/judgeset-judge_sheet.json',
          'scripts/experiments/judgeset_score.py',
          'docs/pg-notes/scripts/judgeset_falsify.py'],
    false);


-- Only the latest verdict on a claim can be news.
--
-- 0023's verdict_stale() reported every verdict ever recorded, so a superseded one stayed in
-- the report forever with no way to clear it. That is the 0022 failure again from the other
-- direction: an entry nobody can act on trains the reader to skim, and the next real entry is
-- skimmed with it. An older verdict going stale is history; it is still in `verdict` and still
-- joinable, it is just not something to act on.
CREATE VIEW verdict_latest AS
SELECT DISTINCT ON (claim_id) id, claim_id, agent, verdict, scope, at
FROM verdict ORDER BY claim_id, id DESC;

CREATE OR REPLACE FUNCTION verdict_stale()
RETURNS TABLE (verdict_id bigint, claim_id bigint, topic text, claim_text text,
               verdict text, facet text, reached_under text, world_now text,
               reached_at timestamptz)
LANGUAGE sql STABLE AS $$
    SELECT v.id, c.id, c.topic, c.text, v.verdict, s.facet, s.digest, w.digest, v.at
    FROM verdict_latest v
    JOIN claim c          ON c.id = v.claim_id
    JOIN verdict_world vw ON vw.verdict_id = v.id
    JOIN world_snapshot s ON s.id = vw.snapshot_id
    JOIN world_current w  ON w.facet = s.facet
    WHERE s.digest <> w.digest
    ORDER BY v.id, s.facet
$$;

CREATE OR REPLACE VIEW verdict_unanchored AS
SELECT v.id AS verdict_id, c.id AS claim_id, c.topic, c.text, v.verdict, v.at
FROM verdict_latest v
JOIN claim c ON c.id = v.claim_id
WHERE NOT EXISTS (SELECT 1 FROM verdict_world w WHERE w.verdict_id = v.id)
ORDER BY v.id;
