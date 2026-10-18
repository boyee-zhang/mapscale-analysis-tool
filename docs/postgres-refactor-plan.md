# Postgres 改造计划

分支:`refactor/modular-architecture`

## 目标与约束

本次迭代引入 Postgres/PostGIS 作为持久层。双重目标:

1. **产品**:消掉当前靠 Redis KV + 进程内 dict + 每请求打上游 API 支撑的几处硬伤。
2. **学习**:在真实场景下覆盖一组指定的 Postgres 主题(事务隔离、MVCC/VACUUM、索引设计、
   `EXPLAIN`、锁、在线变更)。方案刻意选择能自然暴露这些现象的实现路径。

决策记录:

| 决策 | 结论 | 理由 |
|---|---|---|
| Kafka | **不引入** | `SELECT ... FOR UPDATE SKIP LOCKED` 的 Postgres 队列已能覆盖本项目的事件需求,且避免引入常驻 broker 的运维负担 |
| Postgres 部署 | **本地 docker**(PostGIS) | 需要可自由调整 `autovacuum_*`、`shared_buffers` 等参数,托管服务不开放 |
| ORM | **不用**。`asyncpg` + 手写 SQL | ORM 的"先读再写"惯用法正是本次要研究的竞态/写偏斜的根源;且需要能直接把查询贴进 `psql` 做 `EXPLAIN` |
| Migration | **编号 `.sql` 文件 + 自写 runner** | 需要自己控制事务边界(`CREATE INDEX CONCURRENTLY` 不能在事务块内执行) |
| Redis | 保留,**降级为纯 TTL 缓存** | isochrone / POI / traffic 的缓存它做得很好;不再当数据库用(`raw:*` / `features:*` 这类无 TTL 的长期数据迁入 Postgres) |
| Elasticsearch | 保留 | Postgres 不替代全文/模糊搜索 |

## 什么该进 Postgres,什么不该

判断标准一条:**缓存回答"能不能不再调一次?";数据库回答"能不能问 API 问不出来的问题?"**

如果收益只是快,Redis 就够了。只有具备下列性质之一才值得进 Postgres:

| 性质 | API 为什么给不了 |
|---|---|
| **历史** | PDOK / OSM / H2S / CBS 全都只告诉你"现在"。没有 API 能回答"过去 12 个月某市租金怎么变的" |
| **跨源 join** | PDOK 有地址、OSM 有设施、CBS 有人口、H2S 有房源。没有单个 API 能回答"人口增长 >5% 的区里,步行 10 分钟有超市、月租 <€1500 的房源" |
| **聚合分析** | API 给点查不给 `GROUP BY`。"租金涨幅最快的 10 个市"用 API 做 = 340 次调用 + Python 里算 |

### 关键:历史是唯一无法事后补的东西

速度、join、聚合都可以后补——从 API 重拉一遍即可。**历史不行:今天没存就永久没有了。**

这决定了优先级,也是下面把阶段 1 标为最高优先的原因。

### 逐源判定

| 数据源 | 进 Postgres | 理由 |
|---|---|---|
| **房源快照** | ★★★ 最高 | 房源是**易逝的**:出现又消失,provider 都没有历史 API。`etl.py` 已在算 new/disappeared/booked 的 diff——**这是只有我们有的数据** |
| **OSM POI** | ★★★ | 见阶段 2.5。Overpass 要在 3 个镜像间故障转移,说明在线调用本来就撑不住 |
| **地理编码结果** | ★★★ | API 没有的派生数据,且现在是进程内 dict,冷启动全丢 |
| **市级边界(~340)** | ★★★ | 静态、极小、每次 choropleth 都要 |
| **CBS 区域数据** | ★★☆ | CBS OData 自带全量历史,所以"历史"论据弱。真正理由是 join 能力,以及它现在存在 Redis 的 `raw:*`/`features:*` 里**无 TTL 永久堆积,已经是个糟糕的数据库了** |
| **PDOK 地址(~1000 万)** | ★☆☆ 部分 | 静态参考数据,但 ES 已在做搜索。只存需要 join 的字段 |
| isochrone | ✗ 留 Redis | 派生数据,键空间 =(点 × 分钟 × 模式)无限大,重算便宜,无历史价值 |
| traffic flow | ✗ 留 Redis | 本质实时,5 分钟 TTL 是正确的 |
| Nominatim 结果 | ✗ 留 Redis | 同上 |

### 房源数据能算出的独有指标

这些**只能**从自建快照推出,公开 API 一个都给不了。这是本项目唯一真正的专有数据资产,
其余功能本质都是公开 API 的重新包装:

- **出租周期** — `first_seen_at` 到 status 变 `booked` 的天数。哪个城市/户型/价位最快租掉
- **价格调整行为** — 挂久没租掉的房源是否降价、降多少
- **真实供给流量** — 不是"现在有多少套",而是"每周新放出多少套"
- **撤下但未租掉的比例** — `disappeared` 而非 `booked` 的占比

## 几何类型约定

全部使用 `geometry(..., 4326)` + GiST 索引,需要以米为单位计算距离时就地 `::geography` 转换。

不混用 `geography` 和 `geometry` 列类型——否则 `ST_Contains` 这类运算要反复转型,既丢索引也难读。

## 阶段划分

| 阶段 | 内容 | 覆盖主题 | 产品收益 |
|---|---|---|---|
| **0** | PostGIS 容器 + migration runner + `geocode_cache` + `municipality` | GiST 索引 | 冷启动不再丢地理编码缓存;choropleth 不再每请求打 PDOK WFS |
| **1** | **OSM POI 离线化**:Geofabrik 荷兰 extract 导入 `poi` 表 + `etl_run` 状态机 + 幂等 upsert + 软删 + 哨兵 | 唯一索引做幂等键、部分索引(含 partial 谓词与 HOT 的冲突)、GiST 与 GIN、真实倾斜下的 `EXPLAIN` | **修掉等时圈 POI 永久丢点的正确性 bug**;类别运行时可选;解锁空间 join / 聚合 / KNN / 反向搜索 |
| **2** | `outbox` 表 + SKIP LOCKED worker | 行锁、`FOR UPDATE SKIP LOCKED`、死锁复现与排查 | 需要常驻进程,**前置条件是后端离开 Vercel serverless(尚未规划)** |
| **~~1'~~** | ~~housing ETL 迁入 Postgres~~ **搁置** | — | provider 全部不可达(见 `docs/data-source-feasibility.md`),没有数据可跑。练习内容已搬入阶段 1 |
| **3** | 合成历史快照 + 按 `captured_at` 分区 + 查询调优 + `CREATE STATISTICS` | `EXPLAIN (ANALYZE, BUFFERS)`、Nested Loop vs Hash Join、估算行数偏差、MVCC/死元组/表膨胀/autovacuum 调参 | 可做房价时间序列;AI 分析从"CBS 给什么算什么"变为"用自建历史算" |
| **4** | 给 `housing_listing` 加 `geom` 列 + `CREATE INDEX CONCURRENTLY` + 分批回填 | 大表在线变更 | 空间过滤从前端 Turf 移到 `ST_Contains` |
| **5** | **本次不做**,见下方「未来迭代」 | 连接池、复制、读写分离 | 无 |

### 为什么 POI 是唯一该最先做的那一项

> 本节 2026-10-17 重写。原文论证的是"housing ETL 优先,因为历史不可回填"。
> 该论证**在现有数据源上不成立**:provider 已全部不可达,而 BAG 与 CBS 都整份发布全历史,
> 不存在"每推迟一天就丢一天"。紧迫性只对 EP-Online 的日增量成立,而那个卡在许可未决上。
> 把 listing 场景的紧迫性直接搬到 BAG/CBS 上是一次错误推论,记录在此以免重犯。

POI 离线化是整个计划里**唯一一项做完之后代码更少、bug 更少、能力更强**的改动。
其余每一项都是净增复杂度来换练习价值。判据:

| | POI 进库 | BAG 进库 |
|---|---|---|
| 行数 | 100–200 万(待验) | 999 万 |
| 理由性质 | **能力 + 正确性 + 可靠性** | 主要是**体积** |
| 修掉现有 bug | ✅ 三个(见 1.0 动机) | ❌ 零 |
| 替掉现有上游 | ✅ Overpass 降为 fallback | ❌ 纯新增 |

同时它满足本文开头的 DB 判据:**卖算力/实时性的留在外部,卖数据集而数据集能整份下载的
搬到本地。** Overpass 属于后者,TomTom 属于前者(所以 traffic flow 继续留在 Redis)。

---

## 阶段 0 Spec

### 0.1 容器

扩展现有 `docker-compose.yml`,新增 `postgres` 服务。参数刻意调小,目的是让
Hash Join 落盘、`BUFFERS` 出现真实磁盘读等现象在千万行量级就能观察到,而不必堆到上亿行。

⚠️ **`image:` 一行与原稿不同,原因见本节末。**

```yaml
  postgres:
    image: imresamu/postgis:16-3.4        # 不是 postgis/postgis,见下
    container_name: mapscale-pg
    environment:
      POSTGRES_USER: mapscale
      POSTGRES_PASSWORD: mapscale
      POSTGRES_DB: mapscale
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    command: >
      postgres
      -c shared_buffers=128MB
      -c work_mem=4MB
      -c maintenance_work_mem=64MB
      -c effective_cache_size=256MB
      -c autovacuum_naptime=10s
      -c track_io_timing=on
      -c log_min_duration_statement=100ms
      -c log_lock_waits=on
      -c log_autovacuum_min_duration=0
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U mapscale"]
      interval: 5s
      timeout: 5s
      retries: 10
```

`volumes:` 下加 `pgdata:`。

参数用意:

- `work_mem=4MB` — 排序/哈希超限就落盘,`EXPLAIN ANALYZE` 会显示 `Batches: N  Disk: N kB`
- `shared_buffers=128MB` — `BUFFERS` 里能看到 `read=`(真实磁盘读)而非全是 `hit=`
- `track_io_timing=on` — 计划里显示 I/O 实际耗时
- `autovacuum_naptime=10s` — 观察 autovacuum 行为不用等一分钟
- `log_lock_waits=on` + `log_autovacuum_min_duration=0` — 锁等待和 vacuum 全部进日志

#### 为什么镜像不能用官方的 `postgis/postgis`

原稿写 `postgis/postgis:16-3.4`。**官方镜像只发 amd64**,在 Apple Silicon 上会走
Rosetta/QEMU 模拟。这直接摧毁本阶段的全部目的:本节刻意把 `work_mem`、`shared_buffers`
调小,就是为了让落盘、真实磁盘读、autovacuum 行为在千万行量级可观测——而**模拟层下
每一个 `EXPLAIN ANALYZE` 的时间数字都失去意义**,buffers 还能看,timing 不能看。

改用 **`imresamu/postgis`**(多架构镜像,内容是官方镜像的 arm64 构建)。
另一个多架构选项是 `ghcr.io/baosystems/postgis`。

⚠️ **换镜像必须清空 volume**:data 目录在 initdb 时记录了 glibc 的 collation 版本,
amd64 → arm64 启动会直接报 collation 版本不匹配而拒绝启动。

附带两条本机环境事实:`docker-compose`(带连字符)在这台 Mac 上没装,要用
`docker compose`(空格);`ruff` / `python` 不在 PATH 上,一律 `.venv/bin/python -m <tool>`。

### 0.2 Migration runner

`scripts/migrate.py`,约 40 行。规则:

- migration 文件放 `migrations/`,命名 `NNNN_description.sql`
- 文件名含 `.noxact.sql` 的**不包在事务里**(给 `CREATE INDEX CONCURRENTLY` 用)
- 已应用的版本记录在 `schema_migrations(version text primary key, applied_at timestamptz)`
- 按版本号升序执行未应用的文件;只前滚,不支持 down

### 0.3 连接层

`api/main/db.py`:

- 模块级 `asyncpg.Pool`,在 FastAPI lifespan 里创建/关闭
- 连接串来自 `DATABASE_URL`
- 提供 `acquire()` 和一个 `transaction()` 上下文管理器
- 注册 `jsonb` 的 codec(用 `json.loads`/`json.dumps`),否则 asyncpg 返回字符串

**`DATABASE_URL` 缺失时的行为**:与现有 `UPSTASH_*` 的处理方式一致——降级而非崩溃。
`geocoder` / `boundaries` 回退到当前的进程内缓存 + 直接打 PDOK 的路径。这样阶段 0 可以
独立合入,不阻塞现有部署。

### 0.4 Schema(`migrations/0001_init.sql`)

```sql
CREATE EXTENSION IF NOT EXISTS postgis;

-- 替代 housing/geocoder.py 的进程内 dict
CREATE TABLE geocode_cache (
    address_norm text PRIMARY KEY,
    geom         geometry(Point, 4326) NOT NULL,
    source       text NOT NULL,              -- 'pdok' | 'city_centre_fallback'
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- 替代 housing/boundaries.py 的每请求 PDOK WFS 调用
CREATE TABLE municipality (
    code       text PRIMARY KEY,             -- CBS gemeentecode
    name       text NOT NULL,
    geom       geometry(MultiPolygon, 4326) NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX municipality_geom_gist ON municipality USING GIST (geom);
CREATE INDEX municipality_name_lower ON municipality (lower(name));
```

说明:

- `geocode_cache` 不需要空间索引——只按主键点查。**故意**不建,留到真有范围查询时再加,
  以此体现"索引不是越多越好"。
- `municipality` 的 GiST 索引是给阶段 1/4 的 `ST_Contains` 空间 join 准备的。
- `address_norm` 的归一化必须确定性:转小写、合并连续空白、去掉标点、荷兰邮编统一为
  `1234AB`(无空格)。归一化函数放 Python 侧,写成纯函数并加单元测试——
  规则变了会导致缓存整体失效,所以要可测。

### 0.5 接线

**`api/main/housing/geocoder.py`**
查询顺序改为:`geocode_cache` 表 → PDOK → 回写表。保留城市中心兜底,但要用 `source` 列
标记,避免兜底值被当成精确坐标永久复用。

**`api/main/housing/boundaries.py`**
改为查 `municipality` 表。新增 `scripts/seed_municipalities.py`,从 PDOK CBS WFS 拉一次全量
写入(约 340 个市)。choropleth 的计数逻辑**本阶段不动**,仍在 Python 里做——
把它改成 SQL 空间 join 需要 listings 已在库中,属于阶段 1。

### 0.6 环境变量

`.env` 新增:

```
DATABASE_URL=postgresql://mapscale:mapscale@localhost:5432/mapscale
```

### 0.7 验收标准

1. `docker-compose up -d` 后 `python scripts/migrate.py` 幂等——连跑两次第二次无操作。
2. `python scripts/seed_municipalities.py` 后 `municipality` 表约 340 行,且
   `EXPLAIN` 一个 `ST_Contains` 查询能看到 `Index Scan using municipality_geom_gist`。
3. 重启 uvicorn 后,之前地理编码过的地址**不再**产生 PDOK 请求(看结构化日志确认)。
4. `/housing/choropleth` 的响应时间下降,且日志中不再出现 PDOK WFS 调用。
5. 不设 `DATABASE_URL` 时所有现有端点行为不变(降级路径)。
6. `ruff check api/` 通过。

---

## 阶段 1–4 的目标 Schema

阶段 0 只建前两张表。其余在对应阶段引入,此处记录完整意图以便设计时保持一致。

> ⚠️ **2026-10-18 更正:本节的阶段号是按原编号写的,已过期。** 原稿把 `housing_listing`
> 和 `etl_run` 都标成"阶段 1",但阶段 1 后来被 OSM POI 离线化占用(见「阶段划分」里搁置的
> `1'`)。实际落地情况:
>
> | 表 | 原稿标注 | 实际 |
> |---|---|---|
> | `etl_run` | 阶段 1 | ✅ **已建**,`migrations/0003_poi.sql`,但列与下方不同,见下 |
> | `housing_listing` | 阶段 1 | ⛔ **未建**,provider 全部不可达 |
> | `outbox` | 阶段 2 | ⬜ 未建 |
> | `housing_listing_snapshot` | 阶段 3 | ⛔ 未建,外键指向 `housing_listing` |
>
> **`etl_run` 的实际定义与下方不同,以 `0003_poi.sql` 为准**,差异有理由:
> - 键是自由文本 `source`(`'osm_poi:noord-holland'` / `'housing:h2s'`)而不是
>   `provider_id`——POI 导入、未来的 BAG 导入和 housing provider 要共用同一个状态机,
>   而 POI 导入没有 "provider" 可言。
> - `status` 取值是 `running | succeeded | failed`,不是原稿的四态;`pending` 没有写入方,
>   建它就是建一个永远为空的状态。
> - 多了 `row_count` / `note` 两列(哨兵比较与排错都要读),以及
>   `CHECK ((status='running') = (finished_at IS NULL))`,让部分唯一索引的谓词真的等价于
>   "还在跑"。
> - `etl_run_one_active` 的谓词是 `WHERE finished_at IS NULL`,不是
>   `WHERE status = 'running'`——理由与实测结论见 §「`etl_run_one_active` 为什么这样设计」
>   和 `0003_poi.sql` 的注释。

### ⛔ 阶段 2/3/4 共享的阻塞:`housing_listing` 至今不存在

这三个阶段的产物全部挂在这张表上(2 的 outbox 消费的是 listing 事件,3 的
`housing_listing_snapshot` 外键指向它,4 要给它加 `geom` 列并回填),而它没建,
因为没有任何 provider 能取到行。

必须区分的两件事:

- **练习内容不被阻塞。** 幂等 upsert、状态机、写偏斜、部分唯一索引、哨兵,都与"谁提供行"
  无关。CLAUDE.md 里纠正过一次的正是这一点。
- **数据被阻塞,而且是不可补的那种。** 每推迟一天就永久少一天 listing 历史。

出路四选一(尚未决策,与 `docs/architecture-refactor-plan.md` 的同名小节保持同步):
① 合成历史改到已有 82k 行的 `poi` 上做;② 用 BAG(PDOK WFS,999 万真实住宅)构造实体表;
③ 先把后端迁出 Vercel 直接做阶段 2 的常驻 worker;④ 暂停 PG,转做架构阶段 B/C。

```sql
-- 原「阶段 1」,现搁置(见上):实体表,幂等键在此
CREATE TABLE housing_listing (
    id                bigserial PRIMARY KEY,
    provider_id       text NOT NULL,
    external_id       text NOT NULL,
    city              text NOT NULL,
    municipality_code text REFERENCES municipality(code),
    address           text,
    property_type     text,
    area_m2           numeric,
    price_eur         numeric,
    available_from    date,
    status            text NOT NULL,          -- active | disappeared | booked
    first_seen_at     timestamptz NOT NULL,
    last_seen_at      timestamptz NOT NULL,
    UNIQUE (provider_id, external_id)         -- 幂等键,配合 ON CONFLICT DO UPDATE
);
-- 阶段 4 追加:geom geometry(Point,4326) + CREATE INDEX CONCURRENTLY + 分批回填

-- 原「阶段 1」:ETL run 状态机。⚠️ 已在阶段 1(POI)建成,但实际列与此不同,
-- 以 migrations/0003_poi.sql 为准,差异及理由见本节开头的更正框。
CREATE TABLE etl_run (
    id          bigserial PRIMARY KEY,
    provider_id text NOT NULL,
    status      text NOT NULL,                -- pending | running | completed | failed
    started_at  timestamptz,
    finished_at timestamptz,
    stats       jsonb
);
CREATE UNIQUE INDEX etl_run_one_active
    ON etl_run (provider_id) WHERE status = 'running';

-- 阶段 2:outbox / 任务队列,替代 Kafka
CREATE TABLE outbox (
    id           bigserial PRIMARY KEY,
    topic        text NOT NULL,
    payload      jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    locked_at    timestamptz,
    processed_at timestamptz,
    attempts     int NOT NULL DEFAULT 0
);
CREATE INDEX outbox_pending ON outbox (created_at) WHERE processed_at IS NULL;

-- 阶段 3:append-only 时序快照,会长到千万行
CREATE TABLE housing_listing_snapshot (
    listing_id  bigint NOT NULL REFERENCES housing_listing(id),
    captured_at timestamptz NOT NULL,
    price_eur   numeric,
    status      text,
    PRIMARY KEY (listing_id, captured_at)
) PARTITION BY RANGE (captured_at);
```

### `etl_run_one_active` 为什么这样设计

需求是真实的:防止两个 ETL 实例并发运行(QStash 重复投递,或手动触发撞上定时触发)。

朴素实现 `SELECT count(*) WHERE status='running'` 然后 `INSERT` 在 Read Committed 下是
**典型的写偏斜**:两个事务各自读到 0,各自插入,不变量被破坏。阶段 1 要把三种修法都实现一遍并记录:

| 修法 | 代价 |
|---|---|
| `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE` | 抛 `40001`,必须写重试;SSI 有开销 |
| `pg_advisory_xact_lock(hashtext(provider_id))` | 简单,但不变量藏在应用层,不自文档化 |
| 部分唯一索引(**采用**) | 数据库强制,零额外代码;需处理插入失败的 `23505` |

### 阶段 3 的数据规模

目标是"在 Mac 上能看出问题",不追求绝对量级。配合 0.1 的小参数:

- `housing_listing` 约 5 万行(接近真实量级)
- `housing_listing_snapshot` 约 1000–2000 万行(5 万 × 每日一条 × 约 1 年),磁盘约 1–2 GB

表膨胀观察不依赖规模,只依赖 UPDATE 频次——写个脚本反复全量 UPDATE 状态/价格列来催,
对比 HOT update(不触及索引列)与非 HOT 的差异。

`city` 与 `municipality_code` 高度相关,Postgres 的列独立性假设在此失效,估算行数会严重偏低
——这是 `CREATE STATISTICS ... (dependencies)` 的真实案例,不是构造的。

---

## 多 provider 可扩展性(强制要求)

当前只实现了 `h2s` 一个 provider,但**schema 设计必须为接入多个平台(Pararius / Funda /
Kamernet / 各大 corporatie 等)留出余地**。

Python 层已经是 provider 无关的(`models.py` 的 `HousingProvider` ABC + `registry.py` 自注册),
这是全仓库设计最好的部分。**风险在于 SQL schema 会把它破坏掉**——一旦数据落了库,
schema 里的错误假设就很难改。下面逐条是必须遵守的约束。

### 1. provider 是参照表,不是自由文本列

```sql
CREATE TABLE provider (
    id              text PRIMARY KEY,        -- 'h2s' | 'pararius' | ...
    display_name    text NOT NULL,
    enabled         boolean NOT NULL DEFAULT true,
    price_basis     text NOT NULL,           -- 见第 5 条
    rate_limit_rpm  int,
    config          jsonb NOT NULL DEFAULT '{}',
    last_success_at timestamptz
);
```

`housing_listing.provider_id` 和 `etl_run.provider_id` 都加 FK。好处:引用完整性,
且 per-provider 的限流配置、启用开关、最后成功时间有地方放。

### 2. 唯一键必须是复合的

`UNIQUE (provider_id, external_id)` ——**不能**只用 `external_id`。
不同平台的 ID 空间完全独立,随时会撞。这一点现有 schema 已经是对的。

### 3. ETL 状态机天然支持并行

`etl_run_one_active` 是 `(provider_id) WHERE status = 'running'`,
所以**不同 provider 可以并发跑,同一 provider 不行**。这正是想要的语义,已经是对的。

### 4. diff 范围必须按 provider 隔离,且要防抓取失败被误读为大规模下架

这是**最危险的一个坑**,会直接污染历史数据:

> 如果 diff 不按 provider 隔离,provider A 抓取成功而 B 失败返回空集时,
> B 的全部房源会被标记成 `disappeared`——而这是抓取故障,不是市场事件。

两条强制规则:

- diff 的作用范围 = 单个 provider 的单次 run,不跨 provider
- **哨兵检查**:若本次抓到的数量相比上次下降超过阈值(例如 >50%),
  则将该 run 标记为 `failed` 并**不写 diff**,而不是如实记录"大规模下架"

错误地记录一次大规模下架,会永久污染出租周期、供给流量这些核心指标。
抓取失败丢一天数据是可恢复的,写错数据不是。

### 5. 价格语义因平台而异,必须记录口径

H2S 的报价可能含服务费,其他平台可能不含;有的含水电,有的押金另计。
**直接把各家的 `price_eur` 存进同一列然后跨平台比较,得到的结论是错的。**

处置:`provider.price_basis` 记录原始口径;listing 表同时保留

- `price_raw_eur` — 平台原样给出的数字
- `price_normalized_eur` — 统一口径(建议:不含服务费的基础租金)后的值,供跨平台比较

跨 provider 的聚合查询只允许用 normalized 列。

### 6. 平台特有字段用 jsonb,不要加宽主表

各平台暴露的属性不一样(`energy_label` / `furnished` / `service_costs` / `deposit` /
`min_stay_months` ...)。

- **会用来过滤或聚合的** → 提升为正式列
- **仅展示用的平台特有属性** → `attributes jsonb`

GIN 索引只在确实出现 jsonb 条件查询时再建,不要预先建。

### 7. 跨平台重复房源:现在不实现,但不许假设 listing = 物理房源

同一套公寓可能同时挂在 H2S、Pararius、Funda 上,`external_id` 不同、价格不同。
**朴素统计会重复计数。**

正解是分两层:`property`(物理单元)↔ `listing`(某平台对它的一则广告),多对一。

**决定:现在不建 `property` 表**——只有一个 provider 时无从验证匹配逻辑,属于过度设计。
但有两条约束立刻生效:

- 代码和查询中**不得**出现"listing 数量 = 房源数量"的假设;涉及计数的地方注释说明
  这是"广告条数"而非"房源套数"
- 接入第二个 provider 时,加 `housing_listing.property_id bigint NULL` + 实体匹配逻辑。
  **这正好是一次真实的在线加列 + 分批回填**,与阶段 4 的练习目标重合

### 8. 地理编码缓存与 provider 无关(这是优点)

`geocode_cache` 以归一化地址为主键,不带 `provider_id`。
因此**多个平台登记同一地址时共享缓存**,新接入的 provider 自动受益。
不要为了"数据隔离"给它加 provider 维度——那会白白放弃这个好处。

### 9. 任务队列要能按 provider 分片

阶段 2 的 `outbox` / 队列消费必须避免**一个慢 provider 饿死其他 provider**。
`SELECT ... FOR UPDATE SKIP LOCKED` 的取任务语句需要能按 provider 过滤,
使每个 provider 拥有独立的并发配额(对应 `provider.rate_limit_rpm`)。

### 验收标准

接入第二个 provider 时,**不允许修改任何已有表的结构**(加列可以,改列/改约束不行),
也不允许修改 `domains/housing/` 之外的任何代码。

做不到就说明前面某条约束没落实。

---

## 阶段 1 Spec:OSM POI 离线化

> **2026-10-17 提前到阶段 1。** 原先排在 2.5,排在 housing ETL 之后。两个理由推翻了那个顺序:
> (a) housing 的 provider 已经全部不可达(见 `docs/data-source-feasibility.md`),阶段 1 原本
> 的内容没有数据可跑;(b) 这是整个计划里**唯一一项做完之后代码更少、bug 更少、能力更强**的
> 改动——其余每一项都是净增复杂度。原阶段 1 的练习内容(幂等 upsert、`etl_run` 状态机、
> 哨兵检查)一条不少地搬到这里,而且有真实数据驱动。

### 动机:它修的是三个真实 bug,不只是性能

`upstream/overpass.py:22-24` 用**圆形** `(around:{radius}, lat, lng)` 近似等时圈,
`radius = minutes × SPEED_MAP[profile]`,然后 `MapContainer.jsx` 在**浏览器里**用 Turf
`booleanPointInPolygon` 把圆内的点过滤成等时圈内的点。由此:

1. **永久丢点(数据正确性 bug)**。圆 ≠ 等时圈:10 分钟步行的可达范围受河流、铁路、
   桥梁约束,从来不是 800 m 的圆。**落在等时圈内但圆外的 POI 从来没有被请求过**,
   而前端的 Turf 过滤只能删不能加,所以那些点是永久缺失的,不是被过滤掉的。
2. **点在多边形内的判断跑在客户端**。服务端把整个圆的结果发过去,让浏览器扔掉一部分。
3. **类别硬编码在查询字符串里**。只有 `shop=supermarket|convenience`、
   `leisure=fitness_centre`、`amenity=gym` 四个 tag,请求时无法选类别。
4. 3 镜像故障转移本身就是上游撑不住的证据。

离线化之后 1/2/3 一起消失,并且解锁 Overpass **结构上**做不到的查询:

- 多谓词空间 join:"等时圈内的超市,且距电车站 300 m 内"(`ST_DWithin`)
- 聚合:"每个市镇每类 POI 多少个";配合 BAG 可得"每千套住宅的超市数"
- KNN:"每类最近的 5 个 + 距离"(`<->`)
- `tags jsonb` + GIN:任意属性过滤(品牌、无障碍、菜系)**无需改 schema**
- **反向搜索**:"找出步行 15 分钟内同时有超市 + 健身房 + 小学的所有地点"
  ——需要对成千上万个候选点求值,只有本地数据才可能

### 1.0 范围决策:取宽不取窄

原 spec 写"只筛当前用到的类别,20–50 万行,不要全量导入"。**改为取宽**:

- 窄(现在那四个 tag):约 20–50 万行
- **宽(`amenity|shop|leisure|tourism|healthcare|office|craft`):预计 100–200 万行(待验)**

理由:导入成本差别不大,但窄了以后要重做;而且行数多一个数量级,
类别选择性的 `EXPLAIN` 实验才有意思(超市几千个 vs 餐饮几万个,倾斜是真的)。

仍然**不**全量导入 OSM 所有 node/way——那是另一个量级。

### 1.1 Schema(`migrations/0003_poi.sql`)

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- 1.6 的复合 GiST 实验需要

-- 导入批次。同时服务 POI 和未来的其它 source,所以键是 source 而非 provider_id。
CREATE TABLE etl_run (
    id          bigserial PRIMARY KEY,
    source      text NOT NULL,              -- 'osm_poi' | 'housing:h2s' | ...
    status      text NOT NULL,              -- 'running' | 'succeeded' | 'failed'
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,                -- 写一次就不再改,见下
    row_count   int,
    note        text
);

-- 防止同一 source 的两个导入并发跑(重复投递、手滑点两次)。
-- 谓词刻意建在 finished_at 上而不是 status 上:status 是本表 churn 最高的列,
-- 任何被索引列被更新都会让 HOT update 失效(含 partial index 的谓词列)。
-- ⚠️ 原来这里写"所以 running→succeeded 的那次 UPDATE 仍然可以 HOT"——实测是错的。
-- 那次 UPDATE 本身就在写 finished_at,即谓词列,两种谓词下都不可能 HOT。
-- 实测:只改 note/row_count 的 UPDATE 3/3 是 HOT,收尾那次 0/1。
-- 这个选择真正买到的只是"非收尾的 UPDATE 保持 HOT"+ 将来可加中间状态。
-- 见 docs/pg-notes/stage1-poi-import.md。
CREATE UNIQUE INDEX etl_run_one_active ON etl_run (source) WHERE finished_at IS NULL;

CREATE TABLE poi_category (
    category text PRIMARY KEY,              -- 'supermarket' | 'gym' | ...
    label_en text NOT NULL
);

-- 把 OSM 的脏 tag 收敛成稳定类别。这张表是本阶段真正的价值:
-- "健身房"这一个概念现在散落在 leisure=fitness_centre 和 amenity=gym 两个 tag 上,
-- 而该映射目前埋在一个 f-string 里。
CREATE TABLE poi_category_rule (
    osm_key   text NOT NULL,
    osm_value text NOT NULL,
    category  text NOT NULL REFERENCES poi_category (category),
    priority  smallint NOT NULL DEFAULT 100,  -- 一个 POI 命中多条规则时取最小值
    PRIMARY KEY (osm_key, osm_value)
);

CREATE TABLE poi (
    osm_type       char(1) NOT NULL,         -- 'n' | 'w' | 'r'
    osm_id         bigint NOT NULL,
    category       text NOT NULL REFERENCES poi_category (category),
    name           text,
    geom           geometry(Point, 4326) NOT NULL,
    tags           jsonb NOT NULL DEFAULT '{}',
    first_seen_run bigint NOT NULL REFERENCES etl_run (id),
    -- "最后一次改动它的 run",不是"最后一次见到它的 run"。见 1.4 的实测。
    last_changed_run bigint NOT NULL REFERENCES etl_run (id),
    deleted_at     timestamptz,              -- 软删,见 1.4
    PRIMARY KEY (osm_type, osm_id)           -- 自然键即幂等键,不要代理键
);
```

Schema 上三处和原 spec 不同,都是刻意的:

- **去掉 `bigserial` 代理键**。`(osm_type, osm_id)` 本身就是稳定自然键,代理键只是
  多一个索引和一个必须维护的 `UNIQUE`。
- **原 spec 的两个 GiST 索引是浪费**。`poi_category_geom ON poi USING GIST (geom)
  INCLUDE (category)` 与 `poi_geom_gist` 建在同一列上,只多带一个载荷列;而 PostGIS 的
  GiST 是有损的(阶段 0 已实测到 `Index Cond: geom ~ box` + `Filter: st_contains(...)`
  的两段式),`ST_Contains` 必须回表 recheck,所以 index-only scan 拿不到,`INCLUDE`
  在这里基本无收益。改为下面 1.6 的方案。
- **加 `deleted_at` 软删**,因为刷新必须是 upsert 而非 `TRUNCATE` 重灌。

### 1.2 索引(已按 1.6 的实测定稿)

```sql
-- 唯一的空间读索引。查询形状永远是"这几个类别 + 这个多边形",
-- 所以 category 应该进空间索引本体,而不是放在一个要 planner 去组合的兄弟 btree 里。
CREATE INDEX poi_cat_geom_gist ON poi USING GIST (category, geom) WHERE deleted_at IS NULL;
CREATE INDEX poi_tags_gin ON poi USING GIN (tags jsonb_path_ops);
```

刻意**没有** `GIST(geom)`、`btree(category)`、`btree(last_changed_run)` 三个索引,
理由都是实测的,见 1.6 和 `docs/pg-notes/stage1-poi-import.md`。

`jsonb_path_ops` 体积约为默认的一半,但只支持 `@>`,**不能**回答 `tags ? 'key'`
——存在性检查必须写成 `@> '{"key": ...}'`,否则要换 opclass。

### 1.3 导入(`scripts/import_poi.py`)

不用 `osm2pgsql`——它为渲染设计,会建一堆本项目不需要的表。
也不用 `osmium` 命令行(要 `brew install osmium-tool`,多一个系统级前置),
改用 **pyosmium**(`osmium==4.3.1`,写进 `requirements-local.txt`,API 不引用)。

```
1. 下载 Geofabrik 的省级或全国 .osm.pbf
2. 读 poi_category_rule 拼出 tag 白名单 —— 这张表是"导什么"的唯一真相来源,
   拓宽导入范围是一条 INSERT,不是改代码
3. osmium.FileProcessor(...).with_locations().with_filter(TagFilter(*rules))
   单趟流式遍历 node + way;way 靠 with_locations() 在同一趟里拿到节点坐标
4. COPY 进 staging 表 poi_import(geom_wkb text, tags jsonb)
5. COPY 完再建 poi_import_key,避免边灌边维护索引
6. INSERT ... SELECT 进 poi,SRID、降点、类别归一化全部在 SQL 里做
```

几何语义:

- way(被画成建筑轮廓的超市)要降成点。用 **`ST_PointOnSurface`,不要 `ST_Centroid`**
  ——C 形或环形多边形的质心可能落在多边形外。OSM 轮廓自交的比例在全国量级上不可忽略,
  所以要 `ST_MakeValid` + `ST_CollectionExtract`,降不出点的行由 `WHERE` 丢掉而不是
  让整次导入失败。
- staging 存 **hex WKB 文本**而不是 geometry:pyosmium 产出的是裸 WKB,不带 SRID,
  geometry 列只能要么撒谎(SRID 0)要么拒收 COPY。SRID 在 promotion 时赋。
- 全部 `4326`。Geofabrik 的 pbf 本身就是经纬度,不像 BAG 的 gpkg 是 EPSG:28992。

**relation 跳过**(初版)。画成 multipolygon relation 的 POI(少数大公园、医院院区)
会漏掉;接上需要 pyosmium 的 area 处理,是对文件的第二趟遍历,见 1.8。

### 1.4 刷新语义(原阶段 1 的练习内容搬到这里)

OSM 真的会变,所以周期性重导是真需求,不是为了练习而造的需求。

- **幂等 upsert**:`ON CONFLICT (osm_type, osm_id) DO UPDATE`,键就是自然键。
  ⚠️ **必须带 `DO UPDATE ... WHERE 内容真的变了`**。初版每见到一行就 bump
  `last_seen_run`,结果一次**完全空转**的重导就让堆从 22 MB 涨到 44 MB、空间索引从
  3.4 MB 涨到 6.8 MB,82259 次 UPDATE 里 HOT 为 0(geom 和 last_seen_run 都被索引)。
  autovacuum 把 `n_dead_tup` 清回 0,但空间留在关系内部,索引页也不会被普通 VACUUM 消肿。
- **软删而非 `TRUNCATE`**:本次 run 没见到的行置 `deleted_at = now()`,不物理删除。
  `TRUNCATE` 重灌会在刷新窗口内让 `/pois` 返回空。
  "没见到"靠**反连接 `poi_import`** 判定,而不是找 `last_seen_run` 过期的行——
  后者要求每行都写一次,正是上一条的病根。因此列改名 `last_changed_run`,
  它的索引也随之删掉。
- **软删范围按本次 staging 的 bbox 限定**(`ST_MakeEnvelope` + `ST_Intersects`),
  所以只导一个省不可能把别的省全删掉。这是单省验收能成立的前提。
- **重现则复活**:再次出现时 `deleted_at = NULL`,`first_seen_run` 不动
- **哨兵检查**:本次导入行数比上一次成功 run 下降 **>50%** 时,把 run 标 `failed`
  并且**不写任何软删** ——一次下载失败或 tag 过滤写错绝不能被记成"全国 POI 大规模消失"
- **`etl_run` 状态机**:`running → succeeded | failed`,靠 1.1 的 partial unique index
  防并发

### 1.5 `/pois` 改造

**接口签名不变**,新增一个可选 `categories`(逗号分隔)。

关键便利:`/pois` 的参数 `(lng, lat, minutes, profile)` 与 `/isochrone` **完全相同**,
而等时圈已经按 `iso:{lng:.5f}:{lat:.5f}:{minutes}:{profile}` 缓存 24 小时
(`routers/isochrone.py:15`)。所以 `/pois` 直接读那个 key 就拿到**真多边形**,miss 时才调
ORS。**圆形那个 bug 不需要新增任何上游调用就能修掉。**

⚠️ 两个键的小数位**不一致**:`iso:` 是 `.5f`,`pois:` 是 `.3f`(`routers/pois.py:15`)。
复用必须严格按 `.5f` 拼,否则永远 miss。顺带:这说明键格式**不该在两个 router 里各写
一遍**(违反 CLAUDE.md"常量只定义一次"),抽成 `cache.py` 的一个 helper。

⚠️ **下面这段 SQL 是本文档原稿,它是错的。保留原文以便对照,正确实现见其后。**

```sql
-- ✗ 不要用。原稿。
SELECT osm_type, osm_id, category, name, tags, ST_AsGeoJSON(geom)
FROM poi
WHERE deleted_at IS NULL
  AND ST_Contains(ST_GeomFromGeoJSON($1), geom)
  AND ($2::text[] IS NULL OR category = ANY($2));
```

#### 为什么它错:root cause 是 access method 的能力差异,不是写法风格

实测(Noord-Holland 82259 行,完整输出在 `docs/pg-notes/stage1-poi-import.md`):

| 写法 | category 进 Index Cond? | Rows Removed by Filter | Buffers |
|---|---|---|---|
| `category = ANY($2::text[])` | ✗ 退化成堆上 Filter | 2337 | **1708** |
| `category IN ('a','b','c')` | ✗ 同上(同一个计划节点) | 2337 | **1708** |
| `unnest` + LATERAL,**无**内层 LIMIT | ✗ 被展平成 Join Filter | 5570 | **1756** |
| `unnest` + LATERAL + 内层 LIMIT | ✅ | 8/loop | **80** |

root cause 分三层:

1. **`= ANY(array)` 和 `IN (a,b,c)` 在计划树里是同一个节点:`ScalarArrayOpExpr`。**
   所以"改写成 IN"不是修法,两者字节级等价。原稿 §1.6 的表格里那行
   "`IN (3 个类别)` → 81 buffers,一次索引扫描"**是错的**,已在该节更正。
2. **能不能把 `ScalarArrayOpExpr` 下推成索引条件,是 access method 的属性
   (`pg_am.amsearcharray`),而不是操作符或索引列顺序的属性。**
   btree 声明了 `amsearcharray = true`,所以普通 btree 可以对数组做多次索引扫描;
   **GiST 没有声明**,planner 于是根本没有把它变成 Index Cond 的选项。
3. **`btree_gist` 不改变这一点。** 它提供的是让 `=` 这类 btree 操作符能用在 GiST
   索引列上的**操作符族**,它没有、也不可能给 GiST 这个 access method 加上数组搜索能力。
   这是本阶段最容易误判的一处:装了 `btree_gist` 很容易以为"GiST 现在和 btree 一样了"。

后果是复合 `GIST(category, geom)` 的**第一列直接失效**:索引只能用 geom 那一半,
扫完整个空间子树再把 category 当堆上 filter 算掉,于是 80 → 1708 buffers。

修法只能是**把数组拆成一次一个标量**,因为 GiST 只接受朴素 `=`:

- `unnest($2::text[])` 把数组变成行,`CROSS JOIN LATERAL` 对每行参数化地扫一次索引,
  每次循环的谓词是 `category = c.category`,一个朴素 `=`,GiST 能下推。
- **内层 `LIMIT` 是承重的,不是防御性的。** 没有它,planner 会把子查询
  上拉展平(subquery pullup)回一个 `Join Filter`,1756 buffers 原样回来。
  `LIMIT` 的存在使子查询不可展平。
- 试过 `WITH ... AS MATERIALIZED`,**无效**,实测毫无变化。这条记下来以免再试。

还有两处原稿的次要错误,一并修正:

- **`($2::text[] IS NULL OR category = ANY($2))` 这种"可选参数写成 OR"本身是反模式**:
  `OR` 使整个谓词不可索引,哪怕 `ANY` 的问题不存在也一样。正确做法是空数组时
  在应用层填默认类别(实现里是 `DEFAULT_CATEGORIES`),让 SQL 里永远只有一种形状。
- 原稿 `SELECT ... ST_AsGeoJSON(geom)`,实现改为 `ST_X(geom) AS lon, ST_Y(geom) AS lat`
  ——响应契约是 Overpass 的 `lon`/`lat` 平铺字段,返回 GeoJSON 会让前端两条路径不一致。

#### 实际实现

```sql
WITH shape AS (
    SELECT ST_CollectionExtract(
               ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON($1), 4326)), 3) AS g
)
SELECT p.osm_type, p.osm_id, p.category, p.name, p.tags, p.lon, p.lat
FROM unnest($2::text[]) AS c(category), shape
CROSS JOIN LATERAL (
    SELECT osm_type, osm_id, category, name, tags,
           ST_X(geom) AS lon, ST_Y(geom) AS lat
    FROM poi
    WHERE deleted_at IS NULL
      AND category = c.category
      AND ST_Contains(shape.g, geom)
    LIMIT $3
) p
LIMIT $3
```

`shape` CTE 里三个函数每一个都是必需的,都是实测踩出来的:

- **`ST_SetSRID`** —— 不带 `crs` 成员的 GeoJSON 解析出来是 SRID 0,
  `ST_Contains` 会拒绝混 SRID 比较。而 ORS 返回的 GeoJSON 就是不带 `crs` 的。
- **`ST_MakeValid` + `ST_CollectionExtract(...,3)`** —— **非法多边形不会被拒绝,会被回答。**
  `ST_GeomFromGeoJSON` 照收自交环,`ST_Contains` 随后返回一个**未定义**结果,
  只有一条 `NOTICE: Self-intersection`。实测一个 bowtie 环返回了 28 行。
  等时圈是凹包,自交是 ORS 真的能产出的形状,所以这不是防御性冗余。
  需要 `CollectionExtract` 是因为 `ST_MakeValid` 作用在坏环上会产出
  MultiPolygon 或 GeometryCollection。**在正常多边形上这层包装是免费的**:
  同样 52 行、同样 80 buffers——它每个 shape 只跑一次,不是每行跑一次。

> 教训:**对几何而言,HTTP 200 / 无报错都不是答案正确的证据。** 这一条在同一天命中两次
> ——非法多边形被回答,以及 ORS 的 cycling profile 返回一个小 400 倍的等时圈(见 1.8)。
> 两次都只有量级检查能发现。

**降级要求(不可省)**:`db.enabled()` 为假时走原 Overpass 圆形路径。
Overpass 客户端**保留**,和 `cache.py`、`boundaries.py` 同样的模式——
生产(Vercel)在 Postgres 还是本地的阶段仍然依赖它。
之前讨论里"Overpass 整块删除"的说法与本项目的降级规则冲突,作废。

### 1.6 EXPLAIN 实验(已跑完,结论见下)

问题:`category = ? AND ST_Contains(poly, geom)`,planner 该先走谁?
数据:Noord-Holland 82259 行,`car_parking` 占 40%,`supermarket` 占 1%
——**真实倾斜 + 真实体积**,比阶段 3 造千万行假数据好得多。

结论(buffers hit,完整输出在 `docs/pg-notes/stage1-poi-import.md`):

| 查询 | `GIST(geom)` + `btree(category)` | 复合 `GIST(category, geom)` |
|---|---|---|
| 只给多边形 | 819 | 897(同形计划) |
| `supermarket`(1%) | 512(planner 走 **btree**) | **29** |
| `car_parking`(40%) | 819(planner 走 **GiST**) | **65** |
| ~~`IN (3 个类别)`~~ | — | ~~**81**,一次索引扫描~~ **这行是错的,见下** |

⚠️ **更正:`IN (3 个类别)` 并不会"一次索引扫描"。** 当时那个 81 buffers 的测量把
单值 `=` 的计划误记成了多值的。重测结果:`IN (a,b,c)` 与 `= ANY(array)` 是同一个
`ScalarArrayOpExpr` 节点,**两者都把 category 留在堆上当 filter,1708 buffers**。
GiST 的 access method 没有声明 `amsearcharray`,所以多值谓词不可能下推。
80 buffers 那个数字只有 `unnest` + `CROSS JOIN LATERAL` + 内层 `LIMIT` 能达到。
完整推导在上面的 §1.5。

两点值得单独说:

1. 用那一对索引时,**planner 会按类别选择性在两者之间翻转**;无论选哪个,
   另一个谓词都只能当堆上的 filter 算。
2. 复合索引的收益不在 recheck 更便宜,而在**索引本身只返回 25 行**(而非 802 / 2817)。

复合索引**还能服务只给 `geom` 的谓词**:两个索引都在时 planner 就选了它,
把 `GIST(geom)` 删掉后计划逐字相同。所以 `GIST(geom)` 是纯累赘,已从 0003 删除。
⚠️ 这一条只在 82k 行上验过,全国量级要重测——复合 GiST 做 geom-only 搜索要穿过
每个 category 子树。

第三套方案(按类别的 partial GiST)不再需要:复合索引已经覆盖了它的场景,
而 37 个类别要建 37 个索引,写放大不可接受。

### 1.7 验收标准

> 勾选状态更新至 2026-10-18。**8/9 达成**,唯一真缺的是第 2 条的后半(全国 import)。

1. ✅ `python scripts/migrate.py` 从零应用 0001..0004 成功
2. 🟡 导入在**单个省**上验通(Noord-Holland,82259 行,20 s);**全国 import 仍未跑**
3. ✅ 已做,但**结论与原稿预期相反,原稿的"超集"措辞是错的**:
   对旧代码能表达的那个查询(legacy 的 supermarket/convenience/gym,800 m 圆),
   新旧返回**完全相同的 42 行**,双向集合差为空——不是超集,是相等。
   原因是那 3 个类别里 way 只占 14/1638,所以 node-only 查询几乎没丢东西。
   真正的收益是**旧代码根本无法表达的另外 34 个类别**:car_parking 98% 是 way、
   park 99%、playground 82%、swimming_pool 98%、school 66%。
   一个 4 类别请求返回 96 个元素,其中 84 个是 way。
   ⚠️ 原稿那句"82259 行里 44373 行是 way"虽然数字没错,但**不能**用来支持超集结论
   ——它是全表的比例,不是 legacy 3 类的比例。正确表述见
   `docs/pg-notes/stage1-poi-import.md` 的"Recite as"一节
4. ✅ `categories`(可重复 query 参数)生效,默认值 = legacy 三类,所以默认行为不变;
   `tags` 的 GIN 已验:`tags @> '{"wheelchair":"yes"}'` 命中 4041 行,索引扫描 7 buffers
5. ✅ 刷新跑两次:第二次 `INSERT 0 0`、`n_tup_upd = 0`、`n_dead_tup = 0`(幂等);
   软删 10 行 / 复活 10 行各一个用例通过,`first_seen_run` 保持不变
6. ✅ 哨兵用例:伪造一条 `row_count=300000` 的上一次成功 run,本次 82259 行被判定为
   异常,run 标 `failed`、退出码 1、`poi` 一行未动
7. ✅ `DATABASE_URL` 未设置时 `/pois` 仍然可用(走 Overpass)。
   ⚠️ 验证方式有坑:**必须直接 patch `api.main.db.DATABASE_URL = None`,不能用 `env -u`**
   ——`config.py` 的 `load_dotenv()` 会把 `.env` 重新读进来,取消环境变量毫无作用。
   这次验证顺带抓到一个契约不一致:Postgres 路径返回 `type: "n"`,而 Overpass 返回
   `type: "node"`。已用 `_OSM_TYPE` 映射表修掉
8. ✅ 前端 Turf 过滤已删除(`booleanPointInPolygon` 的 useMemo、`@turf/turf` 与 `useMemo`
   的 import 全部移除),地图 POI 数量 = 接口返回数量。
   顺带:React key 改成 `` `${poi.type}/${poi.id}` ``——node 和 way 的 id 空间重叠,
   而旧代码只有 node 所以掩盖了这个冲突
9. ✅ 1.6 的索引对比记入 `docs/pg-notes/stage1-poi-import.md`

额外完成(原 spec 没要求,但属于本阶段):`/pois` 与 `/isochrone` 收进
`domains/geo/service.py`,键格式统一到 `cache.geo_cache_key()`;等时圈路径
**已对真实 ORS 等时圈端到端验证**(见 1.8)。

### 1.8 已知未解决项

- **等时圈本身仍然来自 ORS。** 本阶段把 POI 那一半变成自有数据,多边形那一半没有。
  ORS 不可用时这个功能照样不可用。出路是自托管 ORS 或 pgRouting,
  工作量大于本阶段全部内容,本次不做。

  ⚠️ **这条风险在 2026-10-18 当天就兑现了,而且兑现的方式和原稿的猜测不同。**
  原稿写 ORS"**封数据中心 IP**(CI 里相关测试被 skip 就是这个原因)"——这个归因
  **是错的,至少对本次故障是错的**。实测:

  - 不带 key → **401,95 ms**;带一个格式正确的假 key → **403,94 ms**。
    鉴权层响应很快,所以既不是 IP 被封也不是 key 失效。
  - 带真 key → 请求体上传完成,然后**永不返回**。`range=60` 和 `range=600` 表现一致,
    所以也不是算力/配额。
  - 决定性的一步是**换端点用同一个 key**:`/v2/matrix/driving-car` **200,240 ms**。
    于是账号、key、配额、路由引擎全部排除。
  - 真正的 root cause:**故障是 per-profile 的。** `foot-walking` 在
    `/isochrones`、`/directions`、`/matrix` 上全部黑洞,而其他 profile 在这三个端点
    上全部 ~150 ms 正常。是上游单个 profile 的路网图坏了。
    我们默认 `walking → foot-walking`,所以每一次调用都撞在坏的那一个上。

  **第二个故障更危险:`cycling-*` 四个 profile 全坏,但返回 HTTP 200。**
  600 s 等时圈的服务端自报数据:foot-hiking `area=1.489 km² / reachfactor=1.0`,
  而四个 cycling 全是 `area=0.031 km² / reachfactor≈0.001`(骑行 10 分钟应约 13 km²,
  小了约 400 倍),`/matrix/cycling-regular` 对相距 800 m 的两点返回 `null` duration。
  因为它是 200 + 合法 GeoJSON,`raise_for_status()`、`domains/geo/service.py` 的
  `except`、以及 `/pois` 的圆形降级**全部看不见它**——cycling 会静默少返回 POI。

  处置:`MODE_MAPPING["walking"]` 改指 `foot-hiking`(按上表实测等价,不是因为它返回 200);
  `cycling` **刻意不改**,因为四个骑行图一样坏,没有健康的可指向,而拿步行/驾车图冒充
  骑行是比上游更糟的谎。详见 `docs/pg-notes/ors-profile-outage.md`。

  顺带纠正一处文档事实:ORS 官方 OpenAPI spec **不在任何 `…/api-docs` 路径**(全 404),
  而在 `https://docs.openrouteservice.org/openrouteservice/v2/docs`。spec 里
  `foot-walking` 和所有 `cycling-*` **仍是当前 profile**,所以这是故障不是下线;
  且 `servers[0]` 已经是 **`https://api.heigit.org`**、路径前缀 `/openrouteservice/v2/…`,
  而 `upstream/ors.py` 还在用未被文档记录的旧 host `api.openrouteservice.org/v2/…`
  (今日实测两者行为逐字相同,所以迁移是一个待决策项,不是 bug)。

  **等时圈路径已端到端验证**(这同时销掉了本阶段"只用手写多边形验过"的遗留项):
  walking 37 个 POI / 0.36 s、driving 195 个、driving 5 类别 631 个(其中 409 个 way),
  日志全部 `shape="isochrone"`,零超时。
- OSM 的 tag 覆盖率非常不均(`opening_hours` 在超市上可能只有三到五成,别的类别更低)。
  在稀疏 tag 上过滤会静默返回极少结果,看起来像坏了。**每个过滤器必须能给出覆盖率**,
  否则不要暴露给用户。
- 一个 POI 命中多条 `poi_category_rule` 时靠 `priority` 定胜负,但初始规则集的
  优先级需要人工定且要有测试。并列时的二级排序是 `category` 字母序,只为保证
  重导不产生假 diff。
- **relation 型 POI 被跳过**(初版)。需要 pyosmium 的 area 处理,是对 pbf 的第二趟遍历。
  等验收标准 3 的超集对比出来再判断值不值得接。
- 规则表里的 `amenity=gym` 在 Noord-Holland **命中 0 条**——所有健身房都是
  `leisure=fitness_centre`。规则本身仍要保留(各地 tag 习惯不同),但这说明
  "规则集是否有用"只能靠实测,不能靠读 OSM wiki。

---

## 实验记录要求

每个阶段把观察到的原始输出归档到 `docs/pg-notes/`,按主题分文件:

- 加索引前后的 `EXPLAIN (ANALYZE, BUFFERS)` 完整对比
- 死锁复现时的 `pg_locks` 查询结果与 Postgres 日志中的死锁报告
- `SERIALIZABLE` 下 `40001` 的重试日志
- `pg_stat_user_tables.n_dead_tup` 随 UPDATE 批次的增长曲线,以及 autovacuum 参数调整前后的差异
- 分批回填的批次大小与锁持有时长

理由:这些原始输出无法事后补写,而它们是"做过"与"能讲清楚"之间的差别。

---

## 未来迭代(本次不做)

### 阶段 5:连接池与复制

与产品需求无关,纯为补齐主题。单独起环境做,不进主干。

- **PgBouncer**。真实动机是 serverless 冷启动耗尽 Postgres 连接数(Vercel + Postgres 的
  经典问题,Neon/Supabase 都以此为卖点)。要踩的坑:transaction pooling 模式下
  prepared statement 和 session 级 `SET` 不可用——asyncpg 默认使用 prepared statement,
  需要显式关闭 statement cache。
- **一主一从 + 读写分离**。`docker-compose` 起两个实例配流复制,把 choropleth / city-report
  这类只读查询指向从库。重点是人为制造复制延迟,观察"刚入库的房源在报表里查不到",
  以及 `synchronous_commit` 各档位的权衡。

### 架构方向(与本计划一致但范围更大)

- 两个 repo 合并为 monorepo。按领域纵切:`spatial` / `search` / `housing` / `regions`,
  每个领域自带 schema、服务、路由。`spatial`(isochrone / 周边 POI / 点面判断)是底座,
  其他领域 import 它而非重写。
- `mapscale-ai-service` 按"有状态 / 无状态"切开:CBS ETL 部分(写数据、需要进 Postgres)
  迁入 Python 成为 `regions` 领域;推理部分(无状态,HTTP 进出,不碰数据库)原样保留 Node,
  可无限期不动。理由是两种语言写同一组表会导致两套 migration 或静默的 schema 漂移。
- 引入常驻 worker(阶段 2)后后端需迁出 Vercel serverless;届时 PgBouncer 的必要性随之消失。
- `feature/search-index-lab` 分支(f84aae0)的 edge n-gram 自动补全与高斯距离衰减
  并入生产 `/search`,该分支本身不合并。
