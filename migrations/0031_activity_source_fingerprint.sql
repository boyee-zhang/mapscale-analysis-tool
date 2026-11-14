-- The derive's complete input set, declared once and fingerprinted, instead of one sentinel
-- per field that someone has to remember to add.
--
-- WHAT WAS WRONG. `bekendmaking_activity_derive` has TWO inputs -- `bb.body_text` and
-- `b.title` -- and only the body was watched: 0030's pending view compared `body_etl_run` and
-- `rule_version` and never mentioned `title`. Measured on the live corpus (falsifier verdict 16
-- on ledger claim 23): **32,329 of 94,183 rows (34.3%) derive their `activity_text` from the
-- title alone** (`activity_src = '{}'`, i.e. no labelled field matched and the fallback ran),
-- and `scripts/import_bekendmaking.py` upserts `ON CONFLICT … SET title = EXCLUDED.title`, a
-- branch **108 rows have already taken**. So a title change left all three new surfaces
-- reporting clean at once:
--
--     bekendmaking_activity_pending rows: 0
--     importer's success note:            pending_after=0
--     corpus digest:  c9849a28c6e31295 -> c9849a28c6e31295   (byte-identical)
--
-- while the stored text was stale. That is the same premise-destruction 0027's own entry
-- records, narrowed from "the whole table" to "one of two inputs" rather than closed.
--
-- WHY A FINGERPRINT AND NOT A THIRD SENTINEL. The obvious patch is to compare
-- `bekendmaking.last_changed_run` the way `body_etl_run` is compared. That is a third watched
-- field, hand-maintained, and it fails identically the next time the extraction reads something
-- new: whoever adds the input has to remember to add the sentinel, and forgetting is silent.
-- A fingerprint over the declared input set inverts that -- adding an input changes the set,
-- which changes every row's fingerprint, which makes every row pending. Nothing to remember.
--
-- `bekendmaking_activity_source` IS that declaration. It is the answer to "what does the
-- extraction actually read", and it is checkable: the derive view references exactly
-- `b.identifier`, `b.title` and `bb.body_text` (measured, by matching `\b(?:b|bb)\.(\w+)` over
-- `pg_get_viewdef`), and nothing else. Everything else `bekendmaking_activity` exposes --
-- gemeente, doc_type, published_at, visitors_peak, body_status -- is passed through, not read.
--
-- WHY THE INPUTS CARRY THEIR OWN STORED HASH. Hashing `body_text` at check time would
-- re-introduce exactly the cost 0027 removed: `body_text` averages 1,424 bytes over 94,183
-- rows and 0027's whole finding was that detoasting it 8 times per row was 95% of
-- `/search/notices`. `md5(text)` is IMMUTABLE (`pg_proc.provolatile = 'i'`), so these can be
-- STORED generated columns, computed once on write. The pending check then reads two 32-byte
-- columns and never touches the text at all.
--
-- The cost, stated: each `ALTER TABLE … ADD COLUMN … GENERATED … STORED` rewrites the table
-- under ACCESS EXCLUSIVE (183 MB for `bekendmaking_body`, 121 MB for `bekendmaking`). One-time,
-- and the alternative -- an expression index plus an index-only scan the planner has to agree
-- to -- is a performance hope rather than a guarantee.
--
-- NULL IS NOT "FRESH". `source_fp` is deliberately nullable and NULL means pending. The
-- backfill below only records a fingerprint for rows whose stored text still equals what the
-- current inputs produce; a row that does not match keeps NULL and is reported as pending until
-- it is re-derived. Backfilling the current fingerprint unconditionally would have declared
-- every already-stale row fresh -- committing, in the migration that exists to fix this bug,
-- exactly the bug.

-- 1. The inputs hash themselves on write.
--
-- `CASE WHEN … IS NULL THEN 'nil'` rather than `md5(coalesce(body_text, ''))`: a 3-character
-- sentinel cannot collide with any 32-character md5 output, where coalescing would make "no
-- body row's text" and "a body whose text is the empty string" hash alike. Those two produce
-- the same extraction today, so the collision is harmless today -- which is exactly how the
-- `{}` vs `{""}` collision in an earlier fingerprint of mine survived until a falsifier found
-- it. A fingerprint that is only accidentally correct is not a fingerprint.
ALTER TABLE bekendmaking      ADD COLUMN title_fp text
    GENERATED ALWAYS AS (md5(title)) STORED;
ALTER TABLE bekendmaking_body ADD COLUMN body_text_fp text
    GENERATED ALWAYS AS (CASE WHEN body_text IS NULL THEN 'nil' ELSE md5(body_text) END) STORED;

COMMENT ON COLUMN bekendmaking.title_fp IS
    'Input fingerprint. title feeds bekendmaking_activity_derive''s fallback; 34.3% of rows '
    'derive from it alone. Generated, so no writer can forget to maintain it.';
COMMENT ON COLUMN bekendmaking_body.body_text_fp IS
    'Input fingerprint. Stored rather than computed at check time: hashing body_text on every '
    'pending check is the detoast cost 0027 removed.';

-- 2. The declaration: every field the extraction reads, in one fingerprint.
--
-- Adding an input means changing this view AND adding a row to `activity_rule`, and the row is
-- what makes the whole corpus pending -- the view alone would silently change every
-- fingerprint with no record of why.
CREATE VIEW bekendmaking_activity_source AS
SELECT b.identifier,
       md5(b.title_fp || E'\x01' || coalesce(bb.body_text_fp, 'nobody')) AS source_fp
FROM bekendmaking b
LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier;

COMMENT ON VIEW bekendmaking_activity_source IS
    'The complete input set of bekendmaking_activity_derive, as one fingerprint per '
    'publication. THIS is the declaration of what the extraction depends on: if the derive '
    'ever reads a field that is not hashed here, a change to that field is invisible to '
    'bekendmaking_activity_pending. Reads only generated 32-byte columns, never body_text.';

-- 3. Which source declaration is current, next to which extractor is current.
--
-- A registry key, not a view name, for the same reason `extractor` is one: a bad value fails at
-- `scripts.activity_rules.SOURCE_VIEWS` instead of being interpolated into SQL. Keeping it in
-- the same row as `extractor` is the point -- a rule is "these inputs, this transformation",
-- and splitting the two is how the version ended up in two places in 0027.
ALTER TABLE activity_rule ADD COLUMN source_view text;
UPDATE activity_rule SET source_view = 'source_view_v1' WHERE version = 1;
ALTER TABLE activity_rule ALTER COLUMN source_view SET NOT NULL;

COMMENT ON COLUMN activity_rule.source_view IS
    'Registry key for the input-set declaration (scripts.activity_rules.SOURCE_VIEWS). Paired '
    'with extractor in the same row: a rule is its inputs AND its transformation.';

-- 4. The derived row records which inputs produced it.
ALTER TABLE bekendmaking_activity_text ADD COLUMN source_fp text;

COMMENT ON COLUMN bekendmaking_activity_text.source_fp IS
    'bekendmaking_activity_source.source_fp as of the deriving statement''s snapshot. NULL '
    'means pending, not fresh: it is what the 0031 backfill left on rows whose stored text no '
    'longer matched their inputs.';

-- Only where the stored result still matches what the current inputs produce. Note what this
-- deliberately does NOT require: that the inputs are unchanged. A title that changed in a way
-- the extraction never reads (the row has an `Omschrijving`, so the fallback never ran) leaves
-- the output identical, and such a row IS current -- recording today's fingerprint for it is
-- correct, not a shortcut. The one pass over the derive view here is the ~2 s unbounded read
-- 0027 removed from the request path; once, in a migration, is where that cost belongs.
UPDATE bekendmaking_activity_text t
SET source_fp = s.source_fp
FROM bekendmaking_activity_source s,
     bekendmaking_activity_derive d
WHERE s.identifier = t.identifier
  AND d.identifier = t.identifier
  AND d.activity_text = t.activity_text
  AND d.activity_src  = t.activity_src;

-- 5. The pending check compares fingerprints, not fields.
--
-- DROP and CREATE rather than CREATE OR REPLACE: `body_etl_run`/`current_body_run` move out of
-- the WHERE clause, and REPLACE can only append columns, never reorder or drop them. Nothing in
-- the database depends on this view -- `scripts/activity_rules.py`,
-- `scripts/derive_activity.py` and `scripts/import_bekendmaking_body.py` query it by name.
--
-- `body_etl_run` stays in the OUTPUT as provenance and leaves the TRIGGER. That is a real
-- change in sensitivity and it is the right direction: a refetch returning byte-identical text
-- used to mark the row pending (new `etl_run`, same bytes) and now does not. It also makes the
-- full `--refetch` that CLAUDE.md records as owed much cheaper, since re-fetching unchanged
-- bodies no longer forces a re-derive of the whole corpus.
DROP VIEW bekendmaking_activity_pending;
CREATE VIEW bekendmaking_activity_pending AS
SELECT b.identifier,
       CASE WHEN t.identifier IS NULL                        THEN 'never_derived'
            WHEN t.source_fp IS NULL                         THEN 'fingerprint_unknown'
            WHEN t.source_fp IS DISTINCT FROM s.source_fp    THEN 'source_changed'
            ELSE 'rule_changed' END AS why,
       t.rule_version,
       t.source_fp,
       s.source_fp AS current_source_fp,
       t.body_etl_run, bb.etl_run AS current_body_run
FROM bekendmaking b
JOIN bekendmaking_activity_source s ON s.identifier = b.identifier
LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
LEFT JOIN bekendmaking_activity_text t ON t.identifier = b.identifier
WHERE t.identifier IS NULL
   OR t.source_fp IS DISTINCT FROM s.source_fp
   OR t.rule_version IS DISTINCT FROM activity_rule_current();

COMMENT ON VIEW bekendmaking_activity_pending IS
    'Publications whose activity_text is missing, was derived from inputs that have since '
    'changed, or was derived under a rule version that is no longer current. Empty is the '
    'normal state. The staleness test is a fingerprint over the declared input set '
    '(bekendmaking_activity_source), not a list of watched fields -- so adding an input to the '
    'extraction cannot silently go unwatched.';
