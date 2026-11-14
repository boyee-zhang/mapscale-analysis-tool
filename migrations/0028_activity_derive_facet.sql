-- A path facet for the activity-derivation measurement.
--
-- Claim 17's verdict was anchored to `corpus` + `graph` because the falsifier declined to use
-- `code`, and it was right to: `probe_code` digests HEAD plus the dirty working tree, so
-- writing the note that a verdict's own SCOPE asks for expires that verdict. That is the 0023
-- failure CLAUDE.md records, and the fix 0025 shipped is exactly this -- a facet defined by the
-- paths a measurement actually depends on.
--
-- So: the files that decide whether "/search/notices ranking is ~2 ms rather than ~2 s" is
-- still true. The extraction patterns, the migration that stored them, the SQL builders that
-- read the result, and the script that keeps it current. Editing a note or a test does not
-- move this digest; editing any of these four does.
--
-- `corpus` stays a default anchor and is load-bearing here for a separate reason: the whole
-- measurement is specific to 94,183 rows, and the plan that produced it could change at a
-- different table size. One imported row moves that digest, which is correct.
INSERT INTO world_facet (name, probe, paths, default_anchor) VALUES (
    'activity_derive',
    'sha256 of each path below',
    ARRAY['migrations/0015_bekendmaking_activity.sql',
          'migrations/0027_activity_text_derived.sql',
          'api/main/domains/search/service.py',
          'scripts/derive_activity.py'],
    false);
