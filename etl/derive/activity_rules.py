"""The one place that knows how to derive `activity_text` for a set of identifiers.

Imported by `etl/derive/activity.py` (the batch CLI) and by
`etl/ingest/bekendmaking_body.py` (which derives in the same transaction as the body it
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

WHY STALENESS IS A FINGERPRINT AND NOT A LIST OF WATCHED FIELDS. Verdict 16 on claim 23: the
derive reads `title` as well as `body_text`, 34.3% of rows derive from the title alone, and
only the body was watched -- so a title change left the pending view at 0, the importer's
`pending_after=0` intact and the corpus digest byte-identical while the text was stale. The
fix is 0031's `bekendmaking_activity_source`, which declares the whole input set as one
fingerprint. Adding an input changes that view, which changes every fingerprint, which makes
every row pending. The failure mode of the per-field alternative was that someone has to
remember; this one has nothing to remember.
"""
from __future__ import annotations

from typing import NamedTuple

# The extractor registry. `activity_rule.extractor` is a key in here, not a view name, so a
# bad value fails at this dict rather than being interpolated into SQL. Adding a rule is a row
# in `activity_rule` plus an entry here plus the view it names -- three deliberate steps, none
# of which is editing a number in two places.
EXTRACTORS = {
    # 0015's patterns, as shipped: seven labelled fields, Amsterdam's APV line, and the
    # title/first-body-line fallback.
    "derive_view_v1": "bekendmaking_activity_derive",
}

# The input-set registry, same contract. `activity_rule.source_view` names which declaration of
# "what the extraction reads" is current. It lives in the SAME ROW as `extractor` on purpose: a
# rule is its inputs and its transformation together, and 0027's bug was precisely one half of a
# rule being recorded in a place the other half could not see.
SOURCE_VIEWS = {
    # 0031: md5 over `bekendmaking.title_fp` and `bekendmaking_body.body_text_fp`, the two
    # fields `bekendmaking_activity_derive` actually reads.
    "source_view_v1": "bekendmaking_activity_source",
}


class Rule(NamedTuple):
    """The current rule, resolved. A record rather than a growing tuple: `source_view` was the
    third element to be added here, and positional unpacking at four call sites is how the
    next addition becomes a silent mis-assignment."""

    version: int
    extractor: str
    derive_view: str
    source_view: str


# One batch of the derive view.
#
# The derive view and the source view are read in the SAME STATEMENT, which is what makes the
# stored fingerprint trustworthy: one statement sees one snapshot, so `source_fp` is the
# fingerprint of the very inputs that produced `activity_text`. Computing it in a second
# statement would leave a window in which an input changes between the two and the row records
# a fingerprint for inputs that produced different text -- i.e. a row that is stale and says it
# is current, which is the whole class of bug this subsystem exists to prevent.
#
# Reading it for a bounded identifier list is an index lookup per row; reading it unbounded is
# the ~2 s per query that 0027 removed.
_DERIVE = """
INSERT INTO bekendmaking_activity_text
       (identifier, activity_text, activity_src, rule_version, source_fp, body_etl_run,
        derived_at)
SELECT d.identifier, d.activity_text, d.activity_src, $2, s.source_fp, bb.etl_run, now()
FROM {view} d
JOIN {source} s ON s.identifier = d.identifier
LEFT JOIN bekendmaking_body bb ON bb.identifier = d.identifier
WHERE d.identifier = ANY($1::text[])
ON CONFLICT (identifier) DO UPDATE SET
    activity_text = EXCLUDED.activity_text,
    activity_src  = EXCLUDED.activity_src,
    rule_version  = EXCLUDED.rule_version,
    source_fp     = EXCLUDED.source_fp,
    body_etl_run  = EXCLUDED.body_etl_run,
    derived_at    = EXCLUDED.derived_at
WHERE bekendmaking_activity_text.activity_text IS DISTINCT FROM EXCLUDED.activity_text
   OR bekendmaking_activity_text.activity_src  IS DISTINCT FROM EXCLUDED.activity_src
   OR bekendmaking_activity_text.rule_version  IS DISTINCT FROM EXCLUDED.rule_version
   OR bekendmaking_activity_text.source_fp     IS DISTINCT FROM EXCLUDED.source_fp
   OR bekendmaking_activity_text.body_etl_run  IS DISTINCT FROM EXCLUDED.body_etl_run
RETURNING identifier
"""

# Every identifier whose text is missing, was derived from inputs that have since changed, or
# was derived under an older rule version. Must agree with `bekendmaking_activity_pending` row
# for row -- `etl/verify/activity.py` asserts that across a rule bump, because the two
# disagreeing is the bug verdict 15 found.
#
# `IS DISTINCT FROM` throughout: `source_fp` is nullable (0031's backfill leaves NULL on any row
# whose stored text no longer matched its inputs) and `<>` would silently drop exactly those
# rows from the pending set -- the one group that most needs to be in it.
PENDING = """
SELECT s.identifier
FROM bekendmaking_activity_source s
LEFT JOIN bekendmaking_activity_text t ON t.identifier = s.identifier
WHERE t.identifier IS NULL
   OR t.source_fp IS DISTINCT FROM s.source_fp
   OR t.rule_version IS DISTINCT FROM $1
ORDER BY s.identifier
"""

# The current rule, from the one place that knows. `bekendmaking_activity_pending` reads the
# same row through `activity_rule_current()`, which is the point: 0027 had this number in a
# Python constant AND in the view's WHERE clause, and the first bump made them disagree
# permanently (falsifier verdict 15 on claim 18).
CURRENT = "SELECT version, extractor, source_view FROM activity_rule WHERE is_current"


async def current_rule(conn) -> Rule:
    """The current rule, with its registry keys resolved to object names. Raises, never guesses."""
    row = await conn.fetchrow(CURRENT)
    if row is None:
        raise RuntimeError(
            "no row in activity_rule has is_current -- nothing can be derived, and the "
            "pending view will report every publication. Set one.")
    for key, registry, what in ((row["extractor"], EXTRACTORS, "EXTRACTORS"),
                                (row["source_view"], SOURCE_VIEWS, "SOURCE_VIEWS")):
        if key not in registry:
            raise RuntimeError(
                f"activity_rule version {row['version']} names {key!r}, which is not in "
                f"{what} ({', '.join(sorted(registry))}). Either the registry is behind the "
                f"database or the row is a typo; refusing to guess which.")
    return Rule(row["version"], row["extractor"],
                EXTRACTORS[row["extractor"]], SOURCE_VIEWS[row["source_view"]])


async def derive(conn, identifiers: list[str], rule: Rule) -> int:
    """Derive these identifiers under `rule` (from `current_rule`). Returns rows changed.

    Caller owns the transaction. The importer wraps this with its own upserts so a body and
    its text commit together; the batch CLI uses one transaction per batch so an interrupted
    run leaves a consistent prefix.

    Both view names are formatted in, not bound: they come from the registries above and from
    nowhere else, which `current_rule` checks before they get here. Re-checked at the point of
    formatting, because a caller can construct a `Rule` by hand and a string going into SQL
    deserves the paranoia twice.
    """
    if not identifiers:
        return 0
    if rule.derive_view not in EXTRACTORS.values():
        raise RuntimeError(f"{rule.derive_view!r} is not a registered extractor view")
    if rule.source_view not in SOURCE_VIEWS.values():
        raise RuntimeError(f"{rule.source_view!r} is not a registered source view")
    sql = _DERIVE.format(view=rule.derive_view, source=rule.source_view)
    return len(await conn.fetch(sql, identifiers, rule.version))
