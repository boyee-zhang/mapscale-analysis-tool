-- One source of truth for "which extraction rules are current".
--
-- 0027 shipped the number twice: `bekendmaking_activity_pending` hardcoded `rule_version <> 1`
-- and `scripts/derive_activity.py` carried `RULE_VERSION = 1`. Falsifier verdict 15 on ledger
-- claim 18 showed what that costs the first time a pattern improves and the constant is bumped
-- to 2:
--
--     gmb-9999-000001 | rule_changed |            2 |            1 |                1
--     script's own pending count at $1=2: 0
--
-- The view reports every row as needing work, forever, while the script reports none -- and
-- `derive_activity.py --status` prints both, in that order, contradicting itself. A monitoring
-- surface that is permanently non-empty is the failure CLAUDE.md already records against
-- 0023's verdict_stale: an entry nobody can act on trains the reader to skim, and the next real
-- entry is skimmed with it. Migrations are forward-only and the guard hook denies editing an
-- applied one, so the duplicated literal could not simply be edited out either.
--
-- NOT the same thing as a schema migration version. `schema_migrations` records which DDL has
-- been applied; this records which *transformation* produced the data that DDL holds. They move
-- independently: 0029 changed the schema and not the rules, and a future rule change will add a
-- row here without changing any table.
--
-- `extractor` is a registry key, not a view name: Python resolves it through
-- `scripts.activity_rules.EXTRACTORS`, so a bad value fails loudly at the registry instead of
-- being interpolated into SQL.

CREATE TABLE activity_rule (
    version    int PRIMARY KEY,
    extractor  text NOT NULL,
    note       text,
    is_current boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Exactly one current rule, enforced rather than agreed. A partial unique index is the whole
-- mechanism: two currents, or a bump that forgets to clear the old one, cannot commit.
CREATE UNIQUE INDEX activity_rule_one_current ON activity_rule (is_current) WHERE is_current;

INSERT INTO activity_rule (version, extractor, note, is_current) VALUES
    (1, 'derive_view_v1',
     '0015 patterns (7 labelled fields + Amsterdam APV line + title/first-line fallback), '
     'materialised by 0027', true);

COMMENT ON TABLE activity_rule IS
    'Which activity_text extraction is current. Read by bekendmaking_activity_pending and by '
    'scripts/activity_rules.py, so the two cannot disagree. Not a schema version.';

-- STABLE, not IMMUTABLE: the answer changes when the table does, and a view that inlined it as
-- a constant would be wrong after the first bump -- which is the bug being fixed.
CREATE FUNCTION activity_rule_current() RETURNS int
LANGUAGE sql STABLE AS $$ SELECT version FROM activity_rule WHERE is_current $$;


-- Same shape as 0027's, with the literal replaced by the function and `<>` by
-- `IS DISTINCT FROM`. The comparison matters: `rule_version` is NOT NULL today, but
-- `body_etl_run` is nullable (a publication can exist with no body row), and a future rule
-- table with no current row would make `activity_rule_current()` NULL -- under `<>` every row
-- would quietly leave the pending set, reporting "nothing to do" for a corpus with no rules at
-- all. IS DISTINCT FROM reports all of them instead, which is the right direction to fail.
CREATE OR REPLACE VIEW bekendmaking_activity_pending AS
SELECT b.identifier,
       CASE WHEN t.identifier IS NULL                          THEN 'never_derived'
            WHEN t.body_etl_run IS DISTINCT FROM bb.etl_run     THEN 'body_changed'
            ELSE 'rule_changed' END AS why,
       t.rule_version, t.body_etl_run, bb.etl_run AS current_body_run
FROM bekendmaking b
LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
LEFT JOIN bekendmaking_activity_text t ON t.identifier = b.identifier
WHERE t.identifier IS NULL
   OR t.body_etl_run IS DISTINCT FROM bb.etl_run
   OR t.rule_version IS DISTINCT FROM activity_rule_current();

COMMENT ON VIEW bekendmaking_activity_pending IS
    'Publications whose activity_text is missing, derived from an older body run, or derived '
    'under a rule version that is no longer current. Empty is the normal state.';
