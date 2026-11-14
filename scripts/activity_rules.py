"""The one place that knows how to derive `activity_text` for a set of identifiers.

Imported by `scripts/derive_activity.py` (the batch CLI) and by
`scripts/import_bekendmaking_body.py` (which derives in the same transaction as the body it
just stored). Neither contains a regex, and neither may: the extraction itself lives in the
`bekendmaking_activity_derive` SQL view, which is 0015's expression text. 0015 declined to
materialise this partly because a column "would need a second copy of these patterns inside
the importer, and the two would drift the first time a pattern improved" -- that objection is
answered by there being no second copy, not by it being unlikely.

WHY THE IMPORTER DERIVES IN ITS OWN TRANSACTION. 0027 backfilled inside the migration to avoid
"a window in which `bekendmaking_activity` returns '' for every row and the FTS materialized
view is empty, i.e. search silently returns nothing". The falsifier's verdict 15 on ledger
claim 18 showed that window was not closed, only moved: nothing called the derive step, so
every row imported after 0027 read `''` until someone remembered. Deriving inside the body
upsert's transaction means a committed body always has committed text -- there is no window
left to remember about.
"""
from __future__ import annotations

# The extractor registry. `activity_rule.extractor` is a key in here, not a view name, so a
# bad value fails at this dict rather than being interpolated into SQL. Adding a rule is a row
# in `activity_rule` plus an entry here plus the view it names -- three deliberate steps, none
# of which is editing a number in two places.
EXTRACTORS = {
    # 0015's patterns, as shipped: seven labelled fields, Amsterdam's APV line, and the
    # title/first-body-line fallback.
    "derive_view_v1": "bekendmaking_activity_derive",
}

# One batch of the derive view. Reading it for a bounded identifier list is an index lookup
# per row; reading it unbounded is the ~2 s per query that 0027 removed.
_DERIVE = """
INSERT INTO bekendmaking_activity_text
       (identifier, activity_text, activity_src, rule_version, body_etl_run, derived_at)
SELECT d.identifier, d.activity_text, d.activity_src, $2, bb.etl_run, now()
FROM {view} d
LEFT JOIN bekendmaking_body bb ON bb.identifier = d.identifier
WHERE d.identifier = ANY($1::text[])
ON CONFLICT (identifier) DO UPDATE SET
    activity_text = EXCLUDED.activity_text,
    activity_src  = EXCLUDED.activity_src,
    rule_version  = EXCLUDED.rule_version,
    body_etl_run  = EXCLUDED.body_etl_run,
    derived_at    = EXCLUDED.derived_at
WHERE bekendmaking_activity_text.activity_text IS DISTINCT FROM EXCLUDED.activity_text
   OR bekendmaking_activity_text.activity_src  IS DISTINCT FROM EXCLUDED.activity_src
   OR bekendmaking_activity_text.rule_version  IS DISTINCT FROM EXCLUDED.rule_version
   OR bekendmaking_activity_text.body_etl_run  IS DISTINCT FROM EXCLUDED.body_etl_run
RETURNING identifier
"""

# Every identifier whose text is missing, derived from an older body run, or derived under an
# older rule version. `IS DISTINCT FROM` throughout: body_etl_run is nullable (a publication
# can exist with no body row at all) and `<>` would silently drop those rows from the pending
# set, which is the shape of failure this whole subsystem exists to avoid.
PENDING = """
SELECT b.identifier
FROM bekendmaking b
LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
LEFT JOIN bekendmaking_activity_text t ON t.identifier = b.identifier
WHERE t.identifier IS NULL
   OR t.body_etl_run IS DISTINCT FROM bb.etl_run
   OR t.rule_version IS DISTINCT FROM $1
ORDER BY b.identifier
"""

# The current rule, from the one place that knows. `bekendmaking_activity_pending` reads the
# same row through `activity_rule_current()`, which is the point: 0027 had this number in a
# Python constant AND in the view's WHERE clause, and the first bump made them disagree
# permanently (falsifier verdict 15 on claim 18).
CURRENT = "SELECT version, extractor FROM activity_rule WHERE is_current"


async def current_rule(conn) -> tuple[int, str]:
    """(version, view name) of the current extractor. Raises rather than guessing."""
    row = await conn.fetchrow(CURRENT)
    if row is None:
        raise RuntimeError(
            "no row in activity_rule has is_current -- nothing can be derived, and the "
            "pending view will report every publication. Set one.")
    try:
        return row["version"], EXTRACTORS[row["extractor"]]
    except KeyError:
        raise RuntimeError(
            f"activity_rule version {row['version']} names extractor "
            f"{row['extractor']!r}, which is not in EXTRACTORS "
            f"({', '.join(sorted(EXTRACTORS))}). Either the registry is behind the database "
            f"or the row is a typo; refusing to guess which.") from None


async def derive(conn, identifiers: list[str], rule: tuple[int, str]) -> int:
    """Derive these identifiers under `rule` (from `current_rule`). Returns rows changed.

    Caller owns the transaction. The importer wraps this with its own upserts so a body and
    its text commit together; the batch CLI uses one transaction per batch so an interrupted
    run leaves a consistent prefix.

    The view name is formatted in, not bound: it comes from EXTRACTORS above and from nowhere
    else, which is checked in `current_rule` before it gets here.
    """
    if not identifiers:
        return 0
    version, view = rule
    if view not in EXTRACTORS.values():
        raise RuntimeError(f"{view!r} is not a registered extractor view")
    return len(await conn.fetch(_DERIVE.format(view=view), identifiers, version))
