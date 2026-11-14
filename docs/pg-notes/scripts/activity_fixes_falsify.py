"""Falsifier probes for ledger claim 23. Every case is rolled back."""
import asyncio, os, sys
sys.path.insert(0, "/Users/zby/dev/mapscale-analysis-tool")
import asyncpg
from dotenv import load_dotenv
load_dotenv("/Users/zby/dev/mapscale-analysis-tool/.env")
from scripts.activity_rules import current_rule, derive
from scripts.claims import digest_of, probe_corpus

BODY = ("Stadsdeel: Centrum\nObjectvergunning, ter hoogte van Damstraat 1\n"
        "Omschrijving: het plaatsen van een bouwkraan\n")
PUB = "zz-falsify-23-0001"


async def title_change_stales_text_and_nothing_sees_it(conn):
    ident = await conn.fetchval("""
        SELECT t.identifier FROM bekendmaking_activity_text t
        WHERE t.activity_src = '{}'::text[] AND t.activity_text <> '' LIMIT 1""")
    before_stored = await conn.fetchval(
        "SELECT activity_text FROM bekendmaking_activity_text WHERE identifier=$1", ident)
    c0 = await probe_corpus(conn)
    await conn.execute("UPDATE bekendmaking SET title = 'EEN HEEL ANDERE TITEL' WHERE identifier=$1", ident)
    now_derives = await conn.fetchval(
        "SELECT activity_text FROM bekendmaking_activity_derive WHERE identifier=$1", ident)
    pending = await conn.fetchval("SELECT count(*) FROM bekendmaking_activity_pending")
    read_view = await conn.fetchval(
        "SELECT activity_text FROM bekendmaking_activity WHERE identifier=$1", ident)
    c1 = await probe_corpus(conn)
    print(f"  identifier                 {ident}")
    print(f"  stored activity_text       {before_stored[:60]!r}")
    print(f"  derive view now yields     {now_derives[:60]!r}")
    print(f"  read view still serves     {read_view[:60]!r}")
    print(f"  bekendmaking_activity_pending rows: {pending}")
    print(f"  corpus digest  {digest_of(c0)[:16]} -> {digest_of(c1)[:16]}  "
          f"(activity_digest {c0['activity_digest'][:12]} -> {c1['activity_digest'][:12]}, "
          f"ids_md5 {c0['bekendmaking_ids_md5'][:12]} -> {c1['bekendmaking_ids_md5'][:12]})")
    assert before_stored != now_derives
    return pending == 0 and digest_of(c0) == digest_of(c1)


async def body_text_change_without_run_bump_is_invisible(conn):
    ident = await conn.fetchval("""
        SELECT identifier FROM bekendmaking_activity_text
        WHERE 'omschrijving' = ANY(activity_src) LIMIT 1""")
    c0 = await probe_corpus(conn)
    await conn.execute("UPDATE bekendmaking_body SET body_text = $2 WHERE identifier=$1",
                       ident, "Omschrijving: iets totaal anders\n")
    stored = await conn.fetchval("SELECT activity_text FROM bekendmaking_activity_text WHERE identifier=$1", ident)
    fresh = await conn.fetchval("SELECT activity_text FROM bekendmaking_activity_derive WHERE identifier=$1", ident)
    pending = await conn.fetchval("SELECT count(*) FROM bekendmaking_activity_pending")
    c1 = await probe_corpus(conn)
    print(f"  identifier {ident}")
    print(f"  stored {stored[:50]!r}  vs fresh {fresh[:50]!r}")
    print(f"  pending rows: {pending};  corpus digest {digest_of(c0)[:16]} -> {digest_of(c1)[:16]}")
    return pending == 0 and digest_of(c0) == digest_of(c1)


async def a_derived_row_is_absent_from_the_fts_index(conn):
    run = await conn.fetchval("INSERT INTO etl_run (source, note) VALUES ('falsify23','rolled back') RETURNING id")
    await conn.execute("""
        INSERT INTO bekendmaking (identifier, title, doc_type, published_at, activiteit, url,
                                  raw, first_seen_run, last_changed_run)
        VALUES ($1,'Een titel','omgevingsvergunning',current_date,ARRAY[]::text[],
                'https://example.invalid/x','{}'::jsonb,$2,$2)""", PUB, run)
    await conn.execute("INSERT INTO bekendmaking_body (identifier,status,body_text,etl_run) VALUES ($1,'ok',$2,$3)",
                       PUB, BODY, run)
    await derive(conn, [PUB], await current_rule(conn))
    r = await conn.fetchrow("SELECT activity_text, activity_derived FROM bekendmaking_activity WHERE identifier=$1", PUB)
    in_fts = await conn.fetchval("SELECT count(*) FROM bekendmaking_search_text WHERE identifier=$1", PUB)
    pending = await conn.fetchval("SELECT count(*) FROM bekendmaking_activity_pending")
    hits = await conn.fetchval("""SELECT count(*) FROM bekendmaking_search_text
        WHERE to_tsvector('dutch', activity_text) @@ plainto_tsquery('dutch','bouwkraan')
          AND identifier = $1""", PUB)
    print(f"  activity_derived={r['activity_derived']} text={r['activity_text'][:50]!r}")
    print(f"  pending rows: {pending}   rows in bekendmaking_search_text for it: {in_fts}   FTS hits: {hits}")
    return in_fts == 0


async def derive_zero_rows_vs_nothing_to_do(conn):
    run = await conn.fetchval("INSERT INTO etl_run (source, note) VALUES ('falsify23','rolled back') RETURNING id")
    await conn.execute("""
        INSERT INTO bekendmaking (identifier, title, doc_type, published_at, activiteit, url,
                                  raw, first_seen_run, last_changed_run)
        VALUES ($1,'Een titel','omgevingsvergunning',current_date,ARRAY[]::text[],
                'https://example.invalid/x','{}'::jsonb,$2,$2)""", PUB, run)
    await conn.execute("INSERT INTO bekendmaking_body (identifier,status,body_text,etl_run) VALUES ($1,'ok',$2,$3)",
                       PUB, BODY, run)
    rule = await current_rule(conn)
    first = await derive(conn, [PUB], rule)
    second = await derive(conn, [PUB], rule)
    missing = await derive(conn, ["zz-does-not-exist-at-all"], rule)
    print(f"  derive(new)={first}  derive(again)={second}  derive(identifier not in bekendmaking)={missing}")
    exists = await conn.fetchval("SELECT count(*) FROM bekendmaking_activity_text WHERE identifier=$1", PUB)
    print(f"  activity row exists afterwards: {exists}")
    return second == missing == 0


CASES = [title_change_stales_text_and_nothing_sees_it,
         body_text_change_without_run_bump_is_invisible,
         a_derived_row_is_absent_from_the_fts_index,
         derive_zero_rows_vs_nothing_to_do]


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        for c in CASES:
            print(f"### {c.__name__}")
            tx = conn.transaction()
            await tx.start()
            try:
                got = await c(conn)
                print(f"  -> hole reproduced: {got}\n")
            finally:
                await tx.rollback()
    finally:
        await conn.close()

asyncio.run(main())
