"""Verify the activity-derivation pipeline against a live database.

    .venv/bin/python -m etl.verify.activity

Every case runs inside a transaction that is ROLLED BACK, so the real corpus is never touched.
That is the same shape as etl/verify/ledger.py, and for the same reason: these are
properties of a schema plus a derivation, and asserting them needs a real Postgres.

WHAT EACH CASE IS FOR. All of them come from falsifier verdict 15 on ledger claim 18, which
destroyed the claim that 0027 was complete:

  * 0027 derived inside the migration and nothing called the derive step afterwards, so every
    row imported after it read `activity_text = ''` until someone remembered. The importer now
    derives in the same transaction as the body upsert; `imported_row_is_derived_in_the_same_
    transaction` is that, and `a_body_without_a_derive_is_visible` is the control that proves
    the check can fail.
  * `''` meant both "0015 found no body and no title" and "not derived yet". 0029 split them
    with `activity_derived`; two cases pin both halves, because a flag that is always true is
    not a flag.
  * An import that stores bodies and derives nothing must not read as a success. The importer
    reports `pending_after` and warns on stderr; the pending view is what it reads.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

from etl.derive.activity_rules import PENDING, current_rule, derive  # noqa: E402
from etl.ledger.claims import digest_of, probe_corpus  # noqa: E402

PUB = "zz-verify-activity-0001"
PUB_BARE = "zz-verify-activity-0002"

BODY_TEXT = ("Stadsdeel: Centrum\nObjectvergunning, ter hoogte van Damstraat 1\n"
             "Omschrijving: het plaatsen van een bouwkraan\n")


async def _fixture(conn, *, with_body: bool, ident: str = PUB, title: str = "Een titel") -> int:
    """A publication, optionally with a body. Returns the etl_run id the body claims."""
    run = await conn.fetchval(
        "INSERT INTO etl_run (source, note) VALUES ('verify_activity', 'rolled back') "
        "RETURNING id")
    await conn.execute("""
        INSERT INTO bekendmaking (identifier, title, doc_type, published_at, activiteit, url,
                                  raw, first_seen_run, last_changed_run)
        VALUES ($1, $2, 'omgevingsvergunning', current_date, ARRAY[]::text[],
                'https://example.invalid/x', '{}'::jsonb, $3, $3)""", ident, title, run)
    if with_body:
        await conn.execute("""
            INSERT INTO bekendmaking_body (identifier, status, body_text, etl_run)
            VALUES ($1, 'ok', $2, $3)""", ident, BODY_TEXT, run)
    return run


async def _read(conn, ident: str) -> asyncpg.Record:
    return await conn.fetchrow(
        "SELECT activity_text, activity_src, activity_derived FROM bekendmaking_activity "
        "WHERE identifier = $1", ident)


# ── cases ────────────────────────────────────────────────────────────────────

async def imported_row_is_derived_in_the_same_transaction(conn):
    """What the importer now does: upsert the body, derive the chunk, one transaction."""
    await _fixture(conn, with_body=True)
    await derive(conn, [PUB], await current_rule(conn))
    r = await _read(conn, PUB)
    assert r["activity_derived"] is True, r
    assert r["activity_text"] == "Objectvergunning | het plaatsen van een bouwkraan", r["activity_text"]
    assert sorted(r["activity_src"]) == ["apv_regel", "omschrijving"], r["activity_src"]
    return f"text={r['activity_text'][:46]!r} src={sorted(r['activity_src'])}"


async def a_body_without_a_derive_is_visible(conn):
    """The control. Without this, the case above proves only that the flag is always true."""
    await _fixture(conn, with_body=True)
    r = await _read(conn, PUB)
    assert r["activity_derived"] is False, r
    assert r["activity_text"] == "", r
    pend = [x["identifier"] for x in await conn.fetch(PENDING, (await current_rule(conn)).version)]
    assert PUB in pend, "an underived row must appear in the pending set"
    return f"derived=False text='' and listed as pending ({len(pend)} row(s) pending)"


async def the_empty_string_sentinel_still_means_what_0015_meant(conn):
    """No body and no title: 0015 produced '' on purpose. It must still say so, with
    activity_derived TRUE, or 0029's flag would have replaced one ambiguity with another."""
    await _fixture(conn, with_body=False, ident=PUB_BARE, title="")
    await derive(conn, [PUB_BARE], await current_rule(conn))
    r = await _read(conn, PUB_BARE)
    assert r["activity_derived"] is True, r
    assert r["activity_text"] == "", r
    assert list(r["activity_src"]) == [], r
    return "derived=True text='' src={} -- degraded, not missing"


async def deriving_twice_changes_nothing(conn):
    """Idempotence. The upsert's WHERE clause must suppress a no-op write, or a re-import
    would rewrite every row and make derived_at useless as a signal."""
    await _fixture(conn, with_body=True)
    first = await derive(conn, [PUB], await current_rule(conn))
    second = await derive(conn, [PUB], await current_rule(conn))
    assert (first, second) == (1, 0), (first, second)
    return f"first={first} row changed, second={second}"


async def a_refetched_body_is_rederived(conn):
    """A body re-fetched under a new etl_run must come back as pending, then re-derive."""
    await _fixture(conn, with_body=True)
    await derive(conn, [PUB], await current_rule(conn))
    run2 = await conn.fetchval(
        "INSERT INTO etl_run (source, note) VALUES ('verify_activity2','rb') RETURNING id")
    await conn.execute(
        "UPDATE bekendmaking_body SET body_text = $2, etl_run = $3 WHERE identifier = $1",
        PUB, "Omschrijving: iets anders\n", run2)
    pend = [x["identifier"] for x in await conn.fetch(PENDING, (await current_rule(conn)).version)]
    assert PUB in pend, "a re-fetched body must be pending"
    changed = await derive(conn, [PUB], await current_rule(conn))
    r = await _read(conn, PUB)
    assert changed == 1 and r["activity_text"] == "iets anders", (changed, r["activity_text"])
    return f"pending after refetch, re-derived to {r['activity_text']!r}"


async def a_publication_with_no_body_row_at_all_is_still_derivable(conn):
    """`body_etl_run` is NULL for these. The pending query compares with IS DISTINCT FROM
    precisely so they do not vanish from it; `<>` would make NULL <> NULL unknown."""
    await _fixture(conn, with_body=False, ident=PUB_BARE, title="Alleen een titel")
    pend = [x["identifier"] for x in await conn.fetch(PENDING, (await current_rule(conn)).version)]
    assert PUB_BARE in pend, "a bodyless publication must be pending before derive"
    await derive(conn, [PUB_BARE], await current_rule(conn))
    r = await _read(conn, PUB_BARE)
    run = await conn.fetchval(
        "SELECT body_etl_run FROM bekendmaking_activity_text WHERE identifier = $1", PUB_BARE)
    assert r["activity_derived"] is True and r["activity_text"] == "Alleen een titel", r
    assert run is None, run
    pend2 = [x["identifier"] for x in await conn.fetch(PENDING, (await current_rule(conn)).version)]
    assert PUB_BARE not in pend2, "and must leave the pending set once derived"
    return f"body_etl_run=NULL, text={r['activity_text']!r}, no longer pending"


async def the_embedding_etl_excludes_underived_rows(conn):
    """embed_bekendmaking.py must not treat 'not derived yet' as 'nothing to say'."""
    await _fixture(conn, with_body=True)
    sql = ("SELECT count(*) FROM bekendmaking_activity a "
           "WHERE a.activity_derived AND a.activity_text <> '' AND a.identifier = $1")
    before = await conn.fetchval(sql, PUB)
    under = await conn.fetchval(
        "SELECT count(*) FROM bekendmaking_activity WHERE NOT activity_derived")
    await derive(conn, [PUB], await current_rule(conn))
    after = await conn.fetchval(sql, PUB)
    assert (before, after) == (0, 1), (before, after)
    assert under >= 1, under
    return f"selected by the ETL: before={before} after={after}; underived counter saw {under}"



# ── item 2: the rule version lives in one place ──────────────────────────────

async def the_view_and_the_script_agree_after_a_rule_bump(conn):
    """The defect verdict 15 found, as a test.

    0027 had `rule_version <> 1` in the pending view and `RULE_VERSION = 1` in Python. Bumping
    to 2 made the view report every row forever while the script reported none. Both now read
    `activity_rule`, so this compares the two sets rather than trusting that they match.
    """
    await _fixture(conn, with_body=True)
    await derive(conn, [PUB], await current_rule(conn))

    def sets(rows):
        return {r["identifier"] for r in rows}

    v1_view = sets(await conn.fetch("SELECT identifier FROM bekendmaking_activity_pending"))
    v1_py = sets(await conn.fetch(PENDING, (await current_rule(conn)).version))
    assert v1_view == v1_py == set(), (len(v1_view), len(v1_py))

    # Bump: a new rule row becomes current. Nothing else changes.
    await conn.execute("UPDATE activity_rule SET is_current = false WHERE is_current")
    await conn.execute(
        "INSERT INTO activity_rule (version, extractor, source_view, note, is_current) "
        "VALUES (2, 'derive_view_v1', 'source_view_v1', 'verify_activity, rolled back', true)")
    assert await conn.fetchval("SELECT activity_rule_current()") == 2

    v2_view = sets(await conn.fetch("SELECT identifier FROM bekendmaking_activity_pending"))
    v2_py = sets(await conn.fetch(PENDING, (await current_rule(conn)).version))
    assert v2_view == v2_py, (len(v2_view), len(v2_py))
    assert PUB in v2_view, "the row derived at version 1 must now be pending"

    # Re-deriving ONE row removes exactly that row from both sets. The rest of the corpus is
    # legitimately still pending -- it was all derived at version 1 -- and an earlier version
    # of this case wrongly asserted both sets were empty here, which is a test that did not
    # understand what a rule bump means.
    await derive(conn, [PUB], await current_rule(conn))
    after_view = sets(await conn.fetch("SELECT identifier FROM bekendmaking_activity_pending"))
    after_py = sets(await conn.fetch(PENDING, (await current_rule(conn)).version))
    assert after_view == after_py, (len(after_view), len(after_py))
    assert PUB not in after_view, "the re-derived row must leave both sets"
    assert len(after_view) == len(v2_view) - 1, (len(after_view), len(v2_view))
    return (f"v1: view={len(v1_view)}=py={len(v1_py)} | bumped: view={len(v2_view):,}"
            f"=py={len(v2_py):,} | one re-derived: {len(after_view):,}, agreeing throughout")


async def two_current_rules_cannot_commit(conn):
    """The partial unique index, not an agreement. A second current row would make
    `activity_rule_current()` return an arbitrary one of them."""
    await conn.execute(
        "INSERT INTO activity_rule (version, extractor, source_view, is_current) "
        "VALUES (99, 'derive_view_v1', 'source_view_v1', false)")
    try:
        await conn.execute("UPDATE activity_rule SET is_current = true WHERE version = 99")
    except asyncpg.UniqueViolationError as e:
        return f"rejected: {str(e).split(chr(10))[0][:70]}"
    raise AssertionError("a second is_current row was allowed")


async def an_unregistered_extractor_refuses_rather_than_guessing(conn):
    """A rule row naming an extractor Python does not know must stop the run, not fall back."""
    await conn.execute("UPDATE activity_rule SET is_current = false WHERE is_current")
    await conn.execute(
        "INSERT INTO activity_rule (version, extractor, source_view, is_current) "
        "VALUES (98, 'derive_view_from_the_future', 'source_view_v1', true)")
    try:
        await current_rule(conn)
    except RuntimeError as e:
        assert "not in EXTRACTORS" in str(e), e
        return "RuntimeError naming the registry, as intended"
    raise AssertionError("an unregistered extractor was accepted")


async def no_current_rule_refuses_rather_than_deriving_nothing(conn):
    """With no current rule the pending view must report everything (IS DISTINCT FROM against
    a NULL version), and Python must raise instead of silently deriving at None."""
    await conn.execute("UPDATE activity_rule SET is_current = false WHERE is_current")
    assert await conn.fetchval("SELECT activity_rule_current()") is None
    n = await conn.fetchval("SELECT count(*) FROM bekendmaking_activity_pending")
    assert n > 0, "with no rules, every row is pending -- `<>` would have reported zero"
    try:
        await current_rule(conn)
    except RuntimeError as e:
        assert "is_current" in str(e), e
        return f"view reports {n:,} pending; Python raises instead of deriving at NULL"
    raise AssertionError("current_rule accepted a database with no current rule")



# ── item 3: the anchor can see the derived table ─────────────────────────────

async def emptying_the_derived_table_moves_the_corpus_probe(conn):
    """The falsification the previous probe could not survive.

    `probe_corpus` read only `bekendmaking` and `bekendmaking_locatie`, so deleting every row
    of `bekendmaking_activity_text` left its digest byte-identical while
    `bekendmaking_activity` returned '' for all 94,183 publications. A verdict anchored to
    `corpus` would still have read "current" with its premise gone.
    """
    before = await probe_corpus(conn)
    await conn.execute("DELETE FROM bekendmaking_activity_text")
    after = await probe_corpus(conn)

    assert digest_of(before) != digest_of(after), "the facet digest did not move"
    assert after["activity_count"] == 0, after["activity_count"]
    assert after["activity_digest"] is None, after["activity_digest"]
    assert after["activity_pending_count"] == before["bekendmaking_rows"], after
    # The source-table terms are untouched -- which is exactly why they were not enough.
    assert after["bekendmaking_ids_md5"] == before["bekendmaking_ids_md5"]
    assert after["bekendmaking_locatie_rows"] == before["bekendmaking_locatie_rows"]
    return (f"facet digest {digest_of(before)[:10]} -> {digest_of(after)[:10]}; "
            f"activity_count {before['activity_count']:,} -> 0; pending 0 -> "
            f"{after['activity_pending_count']:,}; source digests unchanged")


async def the_correctness_assertion_fails_when_the_derivation_is_gone(conn):
    """"The probe moved" is not enough on its own -- something has to be checking it. The
    read view is the surface every consumer uses, so that is what is asserted here: with the
    derived table emptied it must report nothing as derived, and the rollback restores it."""
    derived_before = await conn.fetchval(
        "SELECT count(*) FROM bekendmaking_activity WHERE activity_derived")
    assert derived_before > 0, derived_before

    await conn.execute("DELETE FROM bekendmaking_activity_text")
    derived_after = await conn.fetchval(
        "SELECT count(*) FROM bekendmaking_activity WHERE activity_derived")
    empty_after = await conn.fetchval(
        "SELECT count(*) FROM bekendmaking_activity WHERE activity_text = ''")
    assert derived_after == 0, derived_after
    assert empty_after == derived_before, (empty_after, derived_before)
    return (f"derived {derived_before:,} -> 0, and all {empty_after:,} rows read '' "
            f"with activity_derived=false (restored by rollback)")


async def the_activity_digest_moves_with_the_rule_version(conn):
    """The digest covers rule_version, so re-deriving the same text under a new rule is a
    different world. Without that term, a corpus half-migrated to new patterns would digest
    the same as one fully migrated."""
    await _fixture(conn, with_body=True)
    await derive(conn, [PUB], await current_rule(conn))
    d1 = (await probe_corpus(conn))["activity_digest"]
    await conn.execute(
        "UPDATE bekendmaking_activity_text SET rule_version = 2 WHERE identifier = $1", PUB)
    d2 = (await probe_corpus(conn))["activity_digest"]
    assert d1 != d2, (d1, d2)
    return f"{d1[:12]} -> {d2[:12]} on a rule_version change alone"


async def the_activity_digest_does_not_collide_on_empty_vs_quoted_empty_src(conn):
    """`array_to_string(ARRAY[]::text[], ',')` and `array_to_string(ARRAY[''], ',')` are both
    '' -- a collision in an earlier fingerprint of mine that the falsifier found. `::text`
    renders them `{}` and `{""}`."""
    await _fixture(conn, with_body=True)
    await derive(conn, [PUB], await current_rule(conn))
    await conn.execute(
        "UPDATE bekendmaking_activity_text SET activity_src = ARRAY[]::text[] "
        "WHERE identifier = $1", PUB)
    d_empty = (await probe_corpus(conn))["activity_digest"]
    await conn.execute(
        "UPDATE bekendmaking_activity_text SET activity_src = ARRAY[''] WHERE identifier = $1",
        PUB)
    d_quoted = (await probe_corpus(conn))["activity_digest"]
    assert d_empty != d_quoted, (d_empty, d_quoted)
    return f"{{}} -> {d_empty[:12]}, {{\"\"}} -> {d_quoted[:12]}, distinct"


# ── 0031: staleness is a fingerprint over the declared input set ──────────────

# Column-level dependencies of a view, from the catalog rather than from a regex over its
# definition. `pg_depend` records exactly which columns a view reads, which is the question
# being asked -- a regex over `pg_get_viewdef` would have to know the aliases and would quietly
# miss a column referenced in a way the pattern did not anticipate.
_VIEW_DEPS = """
SELECT c.relname AS tbl, a.attname AS col
FROM pg_depend d
JOIN pg_rewrite r ON r.oid = d.objid
JOIN pg_class v   ON v.oid = r.ev_class
JOIN pg_class c   ON c.oid = d.refobjid
JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid
WHERE v.relname = $1 AND d.classid = 'pg_rewrite'::regclass AND d.refobjsubid > 0
  AND c.relname IN ('bekendmaking', 'bekendmaking_body')
"""

# Every STORED generated column on those two tables, with the expression that produces it, so
# the correspondence `title -> title_fp` is read out of the database instead of being written
# down a second time in this file.
_GENERATED = """
SELECT c.relname AS tbl, a.attname AS col, pg_get_expr(ad.adbin, ad.adrelid) AS expr
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
WHERE a.attgenerated = 's' AND c.relname IN ('bekendmaking', 'bekendmaking_body')
"""


async def every_input_the_derive_reads_is_in_the_source_fingerprint(conn):
    """THE structural case, and the reason 0031 is a fingerprint rather than a third sentinel.

    For every column `bekendmaking_activity_derive` reads, there must be a generated
    fingerprint column over that same column, and `bekendmaking_activity_source` must read it.
    Nothing here is hand-maintained: both column sets come from `pg_depend` and the
    `title -> title_fp` correspondence from the generation expressions. So adding an input to
    the extraction without adding it to the declared input set fails HERE, at the next run of
    this script, instead of silently never being watched -- which is exactly how `title` went
    unwatched from 0015 until falsifier verdict 16.
    """
    derive_deps = {(r["tbl"], r["col"]) for r in await conn.fetch(_VIEW_DEPS,
                                                                  "bekendmaking_activity_derive")}
    source_deps = {(r["tbl"], r["col"]) for r in await conn.fetch(_VIEW_DEPS,
                                                                  "bekendmaking_activity_source")}
    generated = await conn.fetch(_GENERATED)
    inputs = {(t, c) for t, c in derive_deps if c != "identifier"}
    assert inputs, "the derive view appears to read nothing -- the dependency query is wrong"
    unwatched = []
    for tbl, col in sorted(inputs):
        fps = [g["col"] for g in generated
               if g["tbl"] == tbl and f"({col}" in g["expr"] or
               (g["tbl"] == tbl and f" {col} " in g["expr"]) or
               (g["tbl"] == tbl and f"({col})" in g["expr"])]
        hashed = [fp for fp in fps if (tbl, fp) in source_deps]
        if not hashed:
            unwatched.append(f"{tbl}.{col}")
    assert not unwatched, (
        f"the derive reads {', '.join(unwatched)} but the source fingerprint does not hash "
        f"it -- a change to that field would be invisible to bekendmaking_activity_pending")
    return (f"derive reads {len(inputs)} input(s) "
            f"({', '.join(f'{t}.{c}' for t, c in sorted(inputs))}), all fingerprinted")


async def the_input_check_refuses_an_unhashed_input(conn):
    """The must-fail half, and the only thing that makes the case above non-vacuous.

    A structural check that cannot fail is decoration. This injects the exact mistake 0031
    exists to prevent -- the extraction grows a third input that the declared input set does
    not hash -- and asserts the check refuses. 0026 is in this repo because 0025 shipped a
    CHECK constraint that could not reject anything; the lesson was that the rejecting half
    has to be exercised, not reasoned about.

    CASCADE is safe here: `bekendmaking_activity` and the `bekendmaking_search_text`
    materialized view both depend on the derive view, and the transaction's rollback restores
    all three with their data.
    """
    vd = await conn.fetchval("SELECT pg_get_viewdef('bekendmaking_activity_derive', true)")
    patched = vd.rstrip().rstrip(";").replace(
        "SELECT b.identifier,\n            b.title,",
        "SELECT b.identifier,\n            b.title,\n            b.abstract,", 1)
    assert "b.abstract" in patched, (
        "could not inject an extra input -- the derive view's text has changed shape and this "
        "control is no longer testing anything, which is worse than it failing")
    await conn.execute("DROP VIEW bekendmaking_activity_derive CASCADE")
    await conn.execute("CREATE VIEW bekendmaking_activity_derive AS " + patched)
    try:
        await every_input_the_derive_reads_is_in_the_source_fingerprint(conn)
    except AssertionError as e:
        assert "abstract" in str(e), str(e)
        return f"refused, naming the input: {str(e)[:72]}…"
    raise AssertionError(
        "the input check PASSED while the derive reads bekendmaking.abstract, which the "
        "source fingerprint does not hash -- the check is vacuous")

async def a_title_change_makes_the_row_pending(conn):
    """The regression test for falsifier verdict 16 on claim 23: `title` is an input, 34.3% of
    rows derive from it alone, and before 0031 changing it left the pending view at 0."""
    await _fixture(conn, with_body=False, title="De oorspronkelijke titel")
    await derive(conn, [PUB], await current_rule(conn))
    assert PUB not in {x["identifier"] for x in
                       await conn.fetch(PENDING, (await current_rule(conn)).version)}
    await conn.execute("UPDATE bekendmaking SET title = $2 WHERE identifier = $1",
                       PUB, "EEN HEEL ANDERE TITEL")
    row = await conn.fetchrow(
        "SELECT why FROM bekendmaking_activity_pending WHERE identifier = $1", PUB)
    pend = {x["identifier"] for x in await conn.fetch(PENDING, (await current_rule(conn)).version)}
    assert row is not None and PUB in pend, f"a title change must be pending; view={row}"
    assert row["why"] == "source_changed", row["why"]
    await derive(conn, [PUB], await current_rule(conn))
    r = await _read(conn, PUB)
    assert r["activity_text"] == "EEN HEEL ANDERE TITEL", r["activity_text"]
    assert PUB not in {x["identifier"] for x in
                       await conn.fetch(PENDING, (await current_rule(conn)).version)}
    return f"why={row['why']}, re-derived to {r['activity_text']!r}, pending cleared"


async def the_pending_view_and_the_script_agree_on_a_title_change(conn):
    """The view and `PENDING` are two expressions of one rule; verdict 15's whole finding was
    the two disagreeing. A title change is the case they most recently disagreed about."""
    await _fixture(conn, with_body=True)
    await derive(conn, [PUB], await current_rule(conn))
    await conn.execute("UPDATE bekendmaking SET title = 'anders' WHERE identifier = $1", PUB)
    view = {r["identifier"] for r in
            await conn.fetch("SELECT identifier FROM bekendmaking_activity_pending")}
    script = {r["identifier"] for r in
              await conn.fetch(PENDING, (await current_rule(conn)).version)}
    assert view == script, (len(view), len(script), list(view ^ script)[:5])
    assert PUB in view, "and the changed row must be in both"
    return f"both report {len(view):,} row(s), {PUB} among them"


async def a_title_change_the_extraction_ignores_still_reports_pending(conn):
    """The honest cost of a fingerprint, asserted so it is recorded rather than discovered.

    This row has an `Omschrijving`, so the title never reaches `activity_text`. Changing it
    produces no output change at all -- and the row still goes pending, because the fingerprint
    is over the inputs, not over the result. It converges on the next derive. Over-reporting is
    the direction CLAUDE.md requires: it is visible, where under-reporting is silence.
    """
    await _fixture(conn, with_body=True, title="Titel die niets doet")
    await derive(conn, [PUB], await current_rule(conn))
    before = (await _read(conn, PUB))["activity_text"]
    await conn.execute("UPDATE bekendmaking SET title = 'iets heel anders' WHERE identifier = $1",
                       PUB)
    pend = {x["identifier"] for x in await conn.fetch(PENDING, (await current_rule(conn)).version)}
    assert PUB in pend, "an input change is pending even when the output cannot change"
    changed = await derive(conn, [PUB], await current_rule(conn))
    after = (await _read(conn, PUB))["activity_text"]
    assert before == after, (before, after)
    assert changed == 1, "the row is rewritten to record the new fingerprint"
    assert PUB not in {x["identifier"] for x in
                       await conn.fetch(PENDING, (await current_rule(conn)).version)}
    return f"pending, text unchanged ({after!r}), fingerprint updated, converged"


async def a_refetch_with_identical_bytes_is_no_longer_pending(conn):
    """0030 triggered on `body_etl_run`, so a re-fetch returning the same bytes re-derived the
    row for nothing. The fingerprint is over content, so it does not. This is what makes the
    owed full `--refetch` cheap: re-fetching unchanged bodies stops forcing a re-derive."""
    await _fixture(conn, with_body=True)
    await derive(conn, [PUB], await current_rule(conn))
    run2 = await conn.fetchval(
        "INSERT INTO etl_run (source, note) VALUES ('verify_activity3','rb') RETURNING id")
    await conn.execute(
        "UPDATE bekendmaking_body SET body_text = $2, etl_run = $3 WHERE identifier = $1",
        PUB, BODY_TEXT, run2)
    pend = {x["identifier"] for x in await conn.fetch(PENDING, (await current_rule(conn)).version)}
    stored_run = await conn.fetchval(
        "SELECT body_etl_run FROM bekendmaking_activity_text WHERE identifier = $1", PUB)
    assert PUB not in pend, "identical bytes under a new run id must NOT be pending"
    assert stored_run != run2, (stored_run, run2)
    return f"etl_run moved {stored_run}->{run2}, bytes identical, not pending"


async def a_null_fingerprint_is_pending_not_fresh(conn):
    """0031's backfill leaves `source_fp` NULL on any row whose stored text no longer matched
    its inputs. NULL must mean pending: `<>` would drop precisely those rows from the set."""
    await _fixture(conn, with_body=True)
    await derive(conn, [PUB], await current_rule(conn))
    await conn.execute(
        "UPDATE bekendmaking_activity_text SET source_fp = NULL WHERE identifier = $1", PUB)
    row = await conn.fetchrow(
        "SELECT why FROM bekendmaking_activity_pending WHERE identifier = $1", PUB)
    pend = {x["identifier"] for x in await conn.fetch(PENDING, (await current_rule(conn)).version)}
    assert row is not None and row["why"] == "fingerprint_unknown", row
    assert PUB in pend, "and the script must agree with the view"
    return f"why={row['why']}, in both sets"


async def the_input_fingerprints_cannot_be_written_by_hand(conn):
    """Generated, not merely conventional. A writer that could set `title_fp` itself could set
    it to the old value, which is the whole failure mode being closed."""
    await _fixture(conn, with_body=True)
    for tbl, col in (("bekendmaking", "title_fp"), ("bekendmaking_body", "body_text_fp")):
        try:
            await conn.execute(
                f"UPDATE {tbl} SET {col} = 'liar' WHERE identifier = $1", PUB)  # noqa: S608
        except asyncpg.PostgresError:
            continue
        raise AssertionError(f"{tbl}.{col} accepted a hand-written value")
    return "both refused: generated always"


async def a_title_change_moves_the_corpus_probe(conn):
    """Falsifier verdict 16's objection against the probe itself: a title change left the
    corpus digest byte-identical. `activity_pending_count` is sourced from the pending view, so
    fixing the view fixes the probe -- asserted here rather than assumed."""
    await _fixture(conn, with_body=False, title="Titel voor de probe")
    await derive(conn, [PUB], await current_rule(conn))
    before = await probe_corpus(conn)
    await conn.execute("UPDATE bekendmaking SET title = 'gewijzigd' WHERE identifier = $1", PUB)
    after = await probe_corpus(conn)
    assert digest_of(before) != digest_of(after), "a title change must move the corpus digest"
    assert after["activity_pending_count"] == before["activity_pending_count"] + 1, (
        before["activity_pending_count"], after["activity_pending_count"])
    assert after["activity_digest"] == before["activity_digest"], (
        "and the stored text has NOT changed -- which is precisely why the old probe missed it")
    return (f"pending {before['activity_pending_count']} -> "
            f"{after['activity_pending_count']}, digest "
            f"{digest_of(before)[:12]} -> {digest_of(after)[:12]}")


CASES = [imported_row_is_derived_in_the_same_transaction,
         a_body_without_a_derive_is_visible,
         the_empty_string_sentinel_still_means_what_0015_meant,
         deriving_twice_changes_nothing,
         a_refetched_body_is_rederived,
         a_publication_with_no_body_row_at_all_is_still_derivable,
         the_embedding_etl_excludes_underived_rows,
         the_view_and_the_script_agree_after_a_rule_bump,
         two_current_rules_cannot_commit,
         an_unregistered_extractor_refuses_rather_than_guessing,
         no_current_rule_refuses_rather_than_deriving_nothing,
         emptying_the_derived_table_moves_the_corpus_probe,
         the_correctness_assertion_fails_when_the_derivation_is_gone,
         the_activity_digest_moves_with_the_rule_version,
         the_activity_digest_does_not_collide_on_empty_vs_quoted_empty_src,
         every_input_the_derive_reads_is_in_the_source_fingerprint,
         the_input_check_refuses_an_unhashed_input,
         a_title_change_makes_the_row_pending,
         the_pending_view_and_the_script_agree_on_a_title_change,
         a_title_change_the_extraction_ignores_still_reports_pending,
         a_refetch_with_identical_bytes_is_no_longer_pending,
         a_null_fingerprint_is_pending_not_fresh,
         the_input_fingerprints_cannot_be_written_by_hand,
         a_title_change_moves_the_corpus_probe]


async def main() -> int:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")
    conn = await asyncpg.connect(dsn)
    bad = 0
    try:
        for case in CASES:
            tx = conn.transaction()
            await tx.start()
            try:
                detail = await case(conn)
                print(f"  ok   {case.__name__:58} {detail}")
            except AssertionError as e:
                bad += 1
                print(f"  FAIL {case.__name__:58} {e}")
            except Exception as e:
                bad += 1
                print(f"  ERROR {case.__name__:57} {type(e).__name__}: {e}")
            finally:
                await tx.rollback()
    finally:
        await conn.close()
    print(f"\n{'ALL PASS' if bad == 0 else str(bad) + ' FAILURES'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
