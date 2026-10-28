import asyncio, asyncpg
DSN="postgresql://mapscale:mapscale@localhost:5432/mapscale"

async def run(iso, with_unique):
    c=await asyncpg.connect(DSN)
    await c.execute("DELETE FROM lab_skew.job;")
    await c.execute("DROP INDEX IF EXISTS lab_skew.job_key_uniq;")
    if with_unique:
        await c.execute("CREATE UNIQUE INDEX job_key_uniq ON lab_skew.job (req_key);")
    await c.close()

    a=await asyncpg.connect(DSN); b=await asyncpg.connect(DSN)
    ta=a.transaction(isolation=iso); tb=b.transaction(isolation=iso)
    await ta.start(); await tb.start()
    await a.fetchval("SELECT 1 FROM lab_skew.job WHERE req_key='k1'")
    await b.fetchval("SELECT 1 FROM lab_skew.job WHERE req_key='k1'")
    log=[]
    # T1 先插并提交
    await a.execute("INSERT INTO lab_skew.job VALUES ('k1','from-T1')")
    # T2 的 INSERT 放后台,它会卡在 T1 未提交的索引项上
    task=asyncio.create_task(b.execute("INSERT INTO lab_skew.job VALUES ('k1','from-T2')"))
    await asyncio.sleep(0.4)
    log.append("T2 INSERT 阻塞中" if not task.done() else "T2 INSERT 未阻塞")
    await ta.commit(); log.append("T1 COMMIT")
    try:
        await asyncio.wait_for(task, timeout=5)
        log.append("T2 INSERT 成功")
    except asyncpg.PostgresError as e:
        log.append(f"T2 INSERT 失败 {e.sqlstate} ({type(e).__name__})")
    try:
        await tb.commit(); log.append("T2 COMMIT")
    except Exception as e:
        log.append(f"T2 COMMIT 失败 {getattr(e,'sqlstate','')}")
    rows=await a.fetch("SELECT payload FROM lab_skew.job WHERE req_key='k1'")
    await a.close(); await b.close()
    tag = "有唯一索引" if with_unique else "无唯一索引"
    print(f"  {tag} / {iso:<16} {' -> '.join(log)}")
    print(f"      => {len(rows)} 行: {[r['payload'] for r in rows]}")

async def main():
    print("【实验 2 重做】T1 先提交,再放 T2 过去")
    for iso in ("read_committed","repeatable_read","serializable"):
        await run(iso, True)
    print()
    print("对照:同样顺序,但没有唯一索引")
    for iso in ("read_committed","serializable"):
        await run(iso, False)

asyncio.run(main())
