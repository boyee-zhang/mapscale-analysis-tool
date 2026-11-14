import asyncio, os, sys
sys.path.insert(0, "/Users/zby/dev/mapscale-analysis-tool")
import asyncpg
from dotenv import load_dotenv
load_dotenv("/Users/zby/dev/mapscale-analysis-tool/.env")
from scripts.claims import digest_of, probe_corpus

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = conn.transaction(); await tx.start()
    try:
        c0 = await probe_corpus(conn)
        await conn.execute("UPDATE activity_rule SET is_current=false WHERE is_current")
        await conn.execute("INSERT INTO activity_rule (version, extractor, note, is_current) "
                           "VALUES (2,'derive_view_v1','falsifier rule bump, no data change',true)")
        c1 = await probe_corpus(conn)
        print("rule bump 1 -> 2, NOT ONE DATA ROW CHANGED:")
        print(f"  corpus digest          {digest_of(c0)[:16]} -> {digest_of(c1)[:16]}")
        print(f"  activity_digest        {c0['activity_digest'][:12]} -> {c1['activity_digest'][:12]}")
        print(f"  activity_pending_count {c0['activity_pending_count']:,} -> {c1['activity_pending_count']:,}")
        print(f"  bekendmaking_ids_md5   {c0['bekendmaking_ids_md5'][:12]} -> {c1['bekendmaking_ids_md5'][:12]}")
        # what corpus does NOT cover
        print("\nkeys probe_corpus reports:", sorted(c0))
    finally:
        await tx.rollback(); await conn.close()
asyncio.run(main())
