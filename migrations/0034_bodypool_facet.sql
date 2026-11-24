-- Declare the `bodypool` path facet: the inputs of the body-representation recall measurement.
--
-- WHY A FACET AND NOT `code`. The measurement reads specific bytes -- one arm's rankings, one
-- session's grades, the frozen set it extends, and the two scripts that build and score it --
-- not "the repository". 0025 records why that distinction is load-bearing and 0023 records the
-- cost of getting it wrong: `probe_code` hashed a COUNT of dirty files, so its digest moved
-- three times in one session while the frozen input stayed byte-identical, and then correcting
-- the notes exactly as a verdict's own SCOPE instructed is what expired that verdict.
--
-- WHY NOT JUST `judgeset`. That facet names `judgeset-{raw,judge_sheet,judgments}.json`,
-- `judgeset_score.py` and `judgeset_falsify.py`. This measurement reads all of those AND five
-- files none of them covers. Widening `judgeset` would move its digest and expire verdict 13,
-- which is anchored to it and has nothing to do with this work. A verdict here anchors to
-- BOTH facets instead, which is what `--facets corpus,judgeset,bodypool` says.
--
-- WHAT IS DELIBERATELY IN THE SET:
--   bodypool-arm.json              the body arm's top-10 per query, its new_pairs, and the
--                                  control band -- the treatment and the control together
--   bodypool-judgments.json        pass 1, judged from a truncated summary. KEPT in the set
--                                  on purpose: it is the input to the before/after that
--                                  measures the display defect, so dropping it would make the
--                                  correction unverifiable
--   bodypool-judgments-v2.json     pass 2, the 206 truncated items re-graded from the full
--                                  summary -- the labels the result now rests on
--   bodypool-calibration_key.json  the 10 grades from 2026-11-13 that the drift check compares
--                                  against; if these move, the two sessions are not one
--                                  instrument and the comparison is not either
--   body_pool.py / body_pool_score.py   the build and the scorer
--
-- WHAT IS DELIBERATELY NOT IN IT. `data/body_vectors.npz` is 295 MB and gitignored, and
-- `probe_paths` digests file by file -- so adding it would make every probe read 295 MB to
-- learn what `body_pool.py` plus the corpus digest already determine. The arm reproduces from
-- it byte-identically on all 40 queries, which is an assertion the experiment makes, not a
-- fact a facet can hold. `bodypool-judge_sheet.json` is also left out: it is derived from
-- bodypool-arm.json and the display SQL, and it changed when the 300-character truncation was
-- removed without any measured quantity changing with it.
--
-- default_anchor = false, like every path facet. 0025's rule: anchoring to a path set is a
-- NARROWING assertion -- "this measurement reads only these bytes" -- and a narrowing
-- assertion has to be made on purpose, never acquired by a later INSERT.

INSERT INTO world_facet (name, probe, paths, default_anchor)
VALUES ('bodypool', 'sha256 of each path below', ARRAY[
    'docs/pg-notes/bodypool-arm.json',
    'docs/pg-notes/bodypool-judgments.json',
    'docs/pg-notes/bodypool-judgments-v2.json',
    'docs/pg-notes/bodypool-calibration_key.json',
    'etl/experiments/body_pool.py',
    'etl/experiments/body_pool_score.py'
], false)
ON CONFLICT (name) DO NOTHING;

-- Self-checking, because an INSERT that matched nothing looks exactly like one that did the
-- job -- 0033's lesson, and 0026 is in this repo because 0025 shipped a CHECK that could not
-- reject anything. Both halves matter: the row must exist with a non-empty path set, and it
-- must NOT have become a default anchor, because that would silently change what every future
-- verdict in this repository anchors to.
DO $$
DECLARE
    n int;
BEGIN
    SELECT coalesce(array_length(paths, 1), 0) INTO n
      FROM world_facet WHERE name = 'bodypool';
    IF n IS NULL OR n < 6 THEN
        RAISE EXCEPTION 'bodypool facet has % paths, expected 6', coalesce(n::text, 'NULL');
    END IF;

    IF EXISTS (SELECT 1 FROM world_facet WHERE name = 'bodypool' AND default_anchor) THEN
        RAISE EXCEPTION 'bodypool must not be a default anchor: a path facet is a narrowing '
                        'assertion and may not be acquired by default';
    END IF;
END $$;
