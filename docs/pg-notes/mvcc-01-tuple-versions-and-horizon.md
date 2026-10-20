# MVCC 实验 01:元组版本、HOT、以及 horizon 被卡住时的膨胀

跑于 2026-10-20,`imresamu/postgis:16-3.4`(PG 16.4),`work_mem=4MB` / `shared_buffers=128MB`。
全部在 `lab_mvcc` schema 里,**不走 migration**,可随时 `DROP SCHEMA lab_mvcc CASCADE`。
这不是产品的一部分。

动机:写偏斜、隔离级别、40001、膨胀、HOT 这五个主题不是五件事,是**同一个机制的五个推论**。
先把机制看成物理的,再去碰上层现象。

## 准备

```sql
CREATE SCHEMA IF NOT EXISTS lab_mvcc;
CREATE EXTENSION IF NOT EXISTS pageinspect;
CREATE TABLE lab_mvcc.acct (id int primary key, bal int not null, note text);
INSERT INTO lab_mvcc.acct VALUES (1,100,'a'),(2,200,'b'),(3,300,'c');
```

## 1. UPDATE 就是 DELETE + INSERT,旧版本物理上留在页里

`UPDATE ... SET bal = 90 WHERE id = 1` 之后,逻辑上还是 3 行,但物理页有 4 个元组:

```
 slot | xmin | xmax | ctid_ptr | xmin_cmt | xmax_cmt | xmax_invalid | hot_updated | heap_only
    1 |  981 |  982 | (0,4)    | t        | t        | f            | t           | f
    2 |  981 |    0 | (0,2)    | t        | f        | t            | f           | f
    3 |  981 |    0 | (0,3)    | t        | f        | t            | f           | f
    4 |  982 |    0 | (0,4)    | t        | f        | t            | f           | t
```

- 槽 1 的 `xmax=982` = "被事务 982 删了",但它**还在**。膨胀的唯一来源就是这个。
- 槽 1 的 `t_ctid=(0,4)` 是**版本链的前向指针**。索引仍指着 `(0,1)`,扫到之后顺链走到槽 4。
- 槽 4 的 `heap_only=t`(`HEAP_ONLY_TUPLE`)= **没有任何索引项指向它**。这就是 HOT。
- 槽 2/3 的 `xmax_invalid=t` 是 hint bit:"xmax 肯定无效,不用查 clog"。

查询用的 infomask 位(`htup_details.h`):

```sql
(t_infomask  &  256) <> 0 AS xmin_cmt       -- HEAP_XMIN_COMMITTED 0x0100
(t_infomask  & 1024) <> 0 AS xmax_cmt       -- HEAP_XMAX_COMMITTED 0x0400
(t_infomask  & 2048) <> 0 AS xmax_invalid   -- HEAP_XMAX_INVALID   0x0800
(t_infomask2 & 16384) <> 0 AS hot_updated   -- HEAP_HOT_UPDATED    0x4000
(t_infomask2 & 32768) <> 0 AS heap_only     -- HEAP_ONLY_TUPLE     0x8000
```

## 2. 同一条 UPDATE,有没有索引决定它是不是 HOT

| # | 语句 | 当时 `bal` 有索引吗 | 结果 |
|---|---|---|---|
| 1 | `SET bal = 90` | ❌ | HOT(槽 4 `heap_only=t`) |
| 2 | `SET bal = 80` | ✅ `CREATE INDEX acct_bal` | **不是 HOT**(槽 5 `heap_only=f`) |
| 3 | `SET note = 'z'` | — (`note` 无索引) | HOT(槽 6 `heap_only=t`) |

```
n_tup_upd | n_tup_hot_upd | n_dead_tup
        3 |             2 |          3
```

**这就是 `etl_run` 那次实测的机制。** 终态 UPDATE 写的 `finished_at` 恰好是部分唯一索引谓词
里的列,所以它不可能是 HOT —— 不是规则有例外,是那条规则正好命中了我在意的那条 UPDATE。
(原始结论见 `stage0`/CLAUDE.md:"应用了规则、实测了、发现规则对我在意的那条 UPDATE 不适用"。)

`pg_stat_force_next_flush()` 是必须的,否则计数器读 0。

## 3. VACUUM 之后:`LP_REDIRECT` 是 HOT 链免索引维护的原因

```
 slot | xmin | xmax | lp_off | lp_len
    1 |      |      |      0 |      0     ← LP_UNUSED,彻底释放
    4 |      |      |      0 |      0     ← LP_UNUSED
    5 |      |      |      6 |      0     ← LP_REDIRECT → 槽 6
    6 |  985 |    0 |   8072 |     34     ← 活的
```

槽 5 不是被释放,而是变成**重定向**。指着 `(0,5)` 的索引项不需要改,它顺着重定向找到槽 6。
另一边 `index scan needed: ... 1 dead item identifiers removed` —— 指着槽 1 的索引项真被删了,
因为那条链整段都死了。

`removable cutoff` 就是 horizon;`0 are dead but not yet removable` 是"没有被卡住"的证据。

## 4. 一个什么都不做的闲置事务,让 VACUUM 收不回一个字节

会话 A(注意:**RR 的快照在第一条查询时才拿,不是 BEGIN 时**):

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT count(*) FROM lab_mvcc.acct;   -- 快照在这里固定
SELECT pg_sleep(25);                  -- 之后什么都不做
```

会话 B,对一张 **3 行**的表做 2000 次 UPDATE:

| | |
|---|---|
| 大小 before | 80 kB |
| 大小 after | **232 kB**(2.9x) |
| `VACUUM` 报告 | `tuples: 0 removed, 2003 remain, **2000 are dead but not yet removable**` |
| `removable cutoff` | **986** —— 不动,死死卡在 A 的 `backend_xmin` 上 |

会话 A 不持有任何行锁,不阻塞任何写入。它只是让 horizon 停住。这就是生产上"没人改代码,表却
一夜涨十倍"的标准形态,也是为什么 `idle_in_transaction_session_timeout` 存在。

A 提交之后,同样的表、同样的 2000 个死元组:

```
removable cutoff: 988            ← 前进了
tuples: 0 removed, 3 remain, 0 are dead but not yet removable
```

## 5. 普通 VACUUM 不把空间还给操作系统

```
VACUUM 后:        232 kB     ← 没变
VACUUM FULL 后:    48 kB
```

普通 VACUUM 只标记"可重用"。`VACUUM FULL` 重写整表并持 `ACCESS EXCLUSIVE` 锁,生产上不可用
(这是 `pg_repack` 存在的理由)。索引同样不收缩:`acct_bal` 从 2 页涨到 8 页,其中 4 页
`currently deleted`。

## 顺带两个真实细节

- `ORDER BY backend_xmin` **报错**:`could not identify an ordering operator for type xid`。
  xid 会回绕,大小比较无意义,要排序得用 `age(xid)`。
- `n_dead_tup` 读到 **4000**,而真值是 2000。它是增量维护的**估算**,VACUUM 也会覆写它,两边
  都计了一次。**别信计数器,信 VACUUM 自己的报告。**(同一类错误:HTTP 200 不是正确答案的证据。)

## 下一步:这些机制直接推出什么

- 快照 = `xmin:xmax:xip_list`(实测 `986:986:`)。Read Committed **每条语句**取新快照,
  Repeatable Read **每个事务**取一次。这一条差别就生成写偏斜、丢失更新、和 40001。
- `xmin`/`xmax` + clog + hint bit = 可见性判定。首次读取已提交元组会**写 hint bit**,
  所以 **SELECT 能产生脏页和 WAL**。待测。
- `relfrozenxid` / freezing / xid 回绕。待测。
