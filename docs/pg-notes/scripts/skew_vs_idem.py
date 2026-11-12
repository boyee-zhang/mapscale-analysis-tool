import asyncio, asyncpg
DSN = "postgresql://mapscale:mapscale@localhost:5432/mapscale"

async def setup(c):
    await c.execute("""
    DROP SCHEMA IF EXISTS lab_skew CASCADE;
    CREATE SCHEMA lab_skew;
    -- 不变式:任何时刻至少有一个医生值班。写的是不同的行。
    CREATE TABLE lab_skew.oncall (doctor text PRIMARY KEY, on_call boolean NOT NULL);
    INSERT INTO lab_skew.oncall VALUES ('alice', true), ('bob', true);
    -- 幂等场景:同一个 request key 只能落一行
    CREATE TABLE lab_skew.job (req_key text NOT NULL, payload text NOT NULL);
    """)

async def reset(c):
    await c.execute("UPDATE lab_skew.oncall SET on_call=true; DELETE FROM lab_skew.job;")

async def write_skew(iso):
    """经典写偏斜:两个事务各读重叠集合,各写不同的行。"""
    a = await asyncpg.connect(DSN); b = await asyncpg.connect(DSN)
    ta = a.transaction(isolation=iso); tb = b.transaction(isolation=iso)
    await ta.start(); await tb.start()
    na = await a.fetchval("SELECT count(*) FROM lab_skew.oncall WHERE on_call")
    nb = await b.fetchval("SELECT count(*) FROM lab_skew.oncall WHERE on_call")
    # 各自判断"还有别人值班",于是各自下班
    await a.execute("UPDATE lab_skew.oncall SET on_call=false WHERE doctor='alice'")
    await b.execute("UPDATE lab_skew.oncall SET on_call=false WHERE doctor='bob'")
    res=[]
    for t,name in ((ta,"T1"),(tb,"T2")):
        try: await t.commit(); res.append(f"{name} COMMIT")
        except Exception as e: res.append(f"{name} {type(e).__name__} {getattr(e,'sqlstate','')}")
    left = await a.fetchval("SELECT count(*) FROM lab_skew.oncall WHERE on_call")
    await a.close(); await b.close()
    print(f"  {iso:<16} 两边都读到 {na}/{nb} 个在值班 -> {', '.join(res)}  => 最终还在值班: {left}"
          + ("   ** 不变式被破坏 **" if left==0 else "   (不变式守住)"))

async def check_then_insert(iso, label):
    """幂等场景:两个事务都先查"存在吗",都说不存在,然后都插。"""
    a = await asyncpg.connect(DSN); b = await asyncpg.connect(DSN)
    ta = a.transaction(isolation=iso); tb = b.transaction(isolation=iso)
    await ta.start(); await tb.start()
    ea = await a.fetchval("SELECT 1 FROM lab_skew.job WHERE req_key='k1'")
    eb = await b.fetchval("SELECT 1 FROM lab_skew.job WHERE req_key='k1'")
    res=[]
    async def ins(conn,t,name):
        try:
            await asyncio.wait_for(conn.execute("INSERT INTO lab_skew.job VALUES ('k1','p')"), timeout=3)
            return None
        except asyncio.TimeoutError: return f"{name} INSERT 被阻塞(等锁)"
        except Exception as e: return f"{name} {getattr(e,'sqlstate','')} {type(e).__name__}"
    r1 = await ins(a,ta,"T1"); r2 = await ins(b,tb,"T2")
    if r1: res.append(r1)
    if r2: res.append(r2)
    for t,name in ((ta,"T1"),(tb,"T2")):
        try: await t.commit(); res.append(f"{name} COMMIT")
        except Exception as e: res.append(f"{name} {getattr(e,'sqlstate','')}")
    n = await a.fetchval("SELECT count(*) FROM lab_skew.job WHERE req_key='k1'")
    await a.close(); await b.close()
    print(f"  {label:<34} 两边都查到'不存在'({ea}/{eb}) -> {', '.join(res)}  => 实际行数: {n}"
          + ("   ** 重复了 **" if n and n>1 else ""))

async def main():
    c = await asyncpg.connect(DSN); await setup(c)
    print("【实验 1】写偏斜本体 —— 写的是不同的行,不变式跨行")
    for iso in ("read_committed","repeatable_read","serializable"):
        await reset(c); await write_skew(iso)
    print()
    print("【实验 2】check-then-insert —— 幂等要解决的那个场景")
    await reset(c)
    await check_then_insert("read_committed",  "无唯一索引 / READ COMMITTED")
    await reset(c)
    await check_then_insert("repeatable_read", "无唯一索引 / REPEATABLE READ")
    await reset(c)
    await check_then_insert("serializable",    "无唯一索引 / SERIALIZABLE")
    await c.execute("CREATE UNIQUE INDEX job_key_uniq ON lab_skew.job (req_key);")
    print("  -- 加上 UNIQUE INDEX job_key_uniq --")
    await reset(c)
    await check_then_insert("read_committed",  "有唯一索引 / READ COMMITTED")
    await reset(c)
    await check_then_insert("serializable",    "有唯一索引 / SERIALIZABLE")
    await c.close()

asyncio.run(main())
