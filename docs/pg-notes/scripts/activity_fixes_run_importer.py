"""Run the REAL importer main() against the scratch db, with the network stubbed."""
import asyncio, os, sys
sys.path.insert(0, "/Users/zby/dev/mapscale-analysis-tool")
import asyncpg
import scripts.import_bekendmaking_body as M

BODY = ("Stadsdeel: Centrum\nObjectvergunning, ter hoogte van Damstraat 1\n"
        "Omschrijving: het plaatsen van een bouwkraan\n")

async def _stub(client, sem, ident):
    return {"status": "ok", "body_text": BODY, "period_raw": "Looptijd: 1-1-2026 t/m 2-1-2026"}
M._one = _stub

DSN = os.environ["DATABASE_URL"]

async def seed(n0, n):
    conn = await asyncpg.connect(DSN)
    run = await conn.fetchval("INSERT INTO etl_run (source, note) VALUES ('seed','x') RETURNING id")
    for i in range(n0, n0 + n):
        await conn.execute("""
            INSERT INTO bekendmaking (identifier, title, doc_type, published_at, activiteit, url,
                                      raw, first_seen_run, last_changed_run)
            VALUES ($1,$2,'omgevingsvergunning',current_date,ARRAY[]::text[],
                    'https://example.invalid/x','{}'::jsonb,$3,$3)
            ON CONFLICT (identifier) DO NOTHING""", f"zz-imp-{i:04d}", f"Titel {i}", run)
    await conn.close()

async def report(tag):
    conn = await asyncpg.connect(DSN)
    r = await conn.fetchrow("""SELECT
        (SELECT count(*) FROM bekendmaking) pubs,
        (SELECT count(*) FROM bekendmaking_body) bodies,
        (SELECT count(*) FROM bekendmaking_activity_text) act,
        (SELECT count(*) FROM bekendmaking_activity_pending) pending,
        (SELECT count(*) FROM bekendmaking_search_text) fts""")
    print(f"[{tag}] pubs={r['pubs']} bodies={r['bodies']} act={r['act']} "
          f"pending={r['pending']} fts_rows={r['fts']}")
    for e in await conn.fetch("SELECT id, source, status, note FROM etl_run ORDER BY id"):
        print(f"   etl_run {e['id']} {e['source']:28} {e['status']:9} {str(e['note'])[:80]}")
    await conn.close()

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "seed":
        asyncio.run(seed(int(sys.argv[2]), int(sys.argv[3])))
    elif cmd == "report":
        asyncio.run(report(sys.argv[2]))
    else:
        sys.argv = ["import_bekendmaking_body.py"] + sys.argv[2:]
        raise SystemExit(asyncio.run(M.main()))
