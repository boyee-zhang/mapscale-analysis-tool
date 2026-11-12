# 写偏斜 vs 幂等:两个不同的问题,一处重叠(实测 2026-10-28)

PostgreSQL 16.4 (aarch64, `imresamu/postgis:16-3.4`),默认 `read committed`。
脚本:`scripts/skew_vs_idem.py`(实验 1 + 2)、`scripts/idem_proper.py`(实验 2 重做)。
用 `asyncpg` 开两条真连接手工交错语句,不是模拟。

起因是一个问题:「写偏斜是不是并发才会遇到的问题,也是幂等要做的事情?」
答案:**是并发问题,但和幂等不是同一个问题** —— 它们只在 check-then-insert 这一种形状上重叠。

## 实验 1:写偏斜本体 —— 写的是不同的行

不变式:`lab_skew.oncall` 里任何时刻至少一个医生 `on_call`。两个事务各读
`SELECT count(*) WHERE on_call`,各读到 2,于是各自把自己那行置 false。

| 隔离级别 | 结果 | 最终 on_call 数 |
|---|---|---|
| `read_committed` | T1 COMMIT, T2 COMMIT | **0 — 不变式被破坏** |
| `repeatable_read` | T1 COMMIT, T2 COMMIT | **0 — 不变式被破坏** |
| `serializable` | T1 COMMIT, T2 **40001** `SerializationError` | 1 — 守住 |

**REPEATABLE READ 挡不住写偏斜。** 这是最反直觉的一条:快照隔离保证你读到一致快照,
不保证你基于该快照做的决定在提交时仍然成立。

两个事务写的是 alice 和 bob **两行**,所以没有行锁冲突、没有 first-updater-wins、
没有任何报错。而「至少一人值班」**无法表达成约束**,所以没有任何索引能救它。
修法只有:SERIALIZABLE,或人为把冲突物化(对一行共享行 `SELECT … FOR UPDATE`,
或把计数维护在单独一行上)。

## 实验 2:check-then-insert —— 幂等要解决的那个场景

两个事务都先 `SELECT 1 FROM job WHERE req_key='k1'`,都得到 NULL,然后都插。
重做版让 T1 先提交,再放 T2 过去(第一版用 `asyncio.wait_for` 超时把 T2 的 INSERT
**取消**了,看不到真正结局 —— 记在这里,免得重犯)。

| 唯一索引 | 隔离级别 | 过程 | 最终行数 |
|---|---|---|---|
| 无 | `read_committed` | T2 INSERT **不阻塞** → 成功 | **2 — 重复** |
| 无 | `repeatable_read` | T2 INSERT 不阻塞 → 成功 | **2 — 重复** |
| 无 | `serializable` | T2 INSERT 成功,**COMMIT 时 40001** | 1 |
| 有 | `read_committed` | T2 **阻塞** → INSERT **23505** `UniqueViolation` | 1 |
| 有 | `repeatable_read` | T2 阻塞 → INSERT **23505** | 1 |
| 有 | `serializable` | T2 阻塞 → INSERT **40001** | 1 |

三条要能背出来:

1. **没有唯一索引时 INSERT 根本不阻塞** —— 没东西可撞。有唯一索引时 T2 卡在 T1
   *尚未提交*的索引项上;这个阻塞发生在**索引层,位于 MVCC 快照之下**,所以
   **不依赖隔离级别**,READ COMMITTED 一样拦得住。这就是唯一索引在这个形状上比
   提高隔离级别更强的原因。
2. **SERIALIZABLE 不靠唯一索引也拦住了重复插入**(40001),靠谓词锁(SSI)。
   快照隔离做不到,因为它不对谓词加锁。
3. **同一个冲突,sqlstate 不同:低隔离级别 `23505`,SERIALIZABLE `40001`。**
   重试语义是反的 —— `23505` = "这活已经干过了,当成功返回";`40001` = "整个事务重来"。
   当成一类处理就是 bug。

## 两者的关系

| | 写偏斜 | 幂等 |
|---|---|---|
| 要不要并发 | **必须** | **不必** —— 重试隔 5 分钟来一次,零并发也重复 |
| 冲突落在 | 不同的行 | 同一个 key |
| 不变式能写成约束吗 | 一般不能 | 能,就是唯一键 |
| 修法 | SERIALIZABLE,或物化冲突 | 唯一索引,任何隔离级别有效 |
| 代价 | 重试 40001 | 一次失败插入 23505 |

重叠的那一处就是 check-then-insert。**反向不成立:唯一索引治不了一般的写偏斜**,
因为那里的写落在不同的行上,没有键可撞。最容易犯的错就是以为"加了唯一键就并发安全"。

## 和本项目的关系

- **ORM 被拒的理由就是这个**:query-then-modify 惯用法本身就是写偏斜的形状。
- `etl_run` 的 `WHERE finished_at IS NULL` 部分唯一索引,是幂等键那一列,不是写偏斜的解。
- 拟建的等时线 worker **两样都要**:`FOR UPDATE SKIP LOCKED` 解决抢任务,
  `(center, profile, seconds) WHERE superseded_at IS NULL` 的部分唯一索引保证只有一行
  current。后者挡的是 check-then-insert,不是跨行不变式。

## 清理
`DROP SCHEMA lab_skew CASCADE;`(`lab_iso` 同理)。两个都是一次性实验 schema,不进 migrations。
