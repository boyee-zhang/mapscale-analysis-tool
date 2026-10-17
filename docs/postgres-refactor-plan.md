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
| **1** | housing ETL 迁入 Postgres:幂等 upsert + `etl_run` 状态机 + 网络 IO 移出事务 + **每日快照开始落库** | 唯一索引做幂等键、部分索引、写偏斜、长事务 | 修掉 ETL 重跑产生重复数据的真实 bug;**开始积累专有历史数据** |
| **2** | `outbox` 表 + SKIP LOCKED worker(地理编码队列、ETL 完成事件) | 行锁、`FOR UPDATE SKIP LOCKED`、死锁复现与排查 | 地理编码由同步阻塞改异步,`/housing/listings` 响应变快 |
| **2.5** | OSM POI 离线化:Geofabrik 荷兰 extract 导入 `poi` 表 + GiST | 真实规模下的 `EXPLAIN`、空间 join、批量刷新 | `/pois` 秒级→毫秒级;删掉 3 镜像故障转移;解锁规模化空间 join |
| **3** | 合成历史快照 + 按 `captured_at` 分区 + 查询调优 + `CREATE STATISTICS` | `EXPLAIN (ANALYZE, BUFFERS)`、Nested Loop vs Hash Join、估算行数偏差、MVCC/死元组/表膨胀/autovacuum 调参 | 可做房价时间序列;AI 分析从"CBS 给什么算什么"变为"用自建历史算" |
| **4** | 给 `housing_listing` 加 `geom` 列 + `CREATE INDEX CONCURRENTLY` + 分批回填 | 大表在线变更 | 空间过滤从前端 Turf 移到 `ST_Contains` |
| **5** | **本次不做**,见下方「未来迭代」 | 连接池、复制、读写分离 | 无 |

### 阶段 1 的优先级高于其表面价值

阶段 1 看起来只是"修 ETL 重复数据的 bug",实际上它是**开始积累唯一专有数据资产的起点**。
每推迟一天,就永久丢失一天的房源历史。

因此:**阶段 1 应尽早落地,不要等 schema 完美。** Schema 允许后续演进——反正阶段 4 本身
就是在练在线加列和分批回填,拿真实的 schema 演进需求来练比造一个假需求更好。

判断是否该推迟某个设计决定的标准:**它会不会导致已采集的数据不可用?** 会,就现在定;
不会,就先采集起来再说。

---

## 阶段 0 Spec

### 0.1 容器

扩展现有 `docker-compose.yml`,新增 `postgres` 服务。参数刻意调小,目的是让
Hash Join 落盘、`BUFFERS` 出现真实磁盘读等现象在千万行量级就能观察到,而不必堆到上亿行。

```yaml
  postgres:
    image: postgis/postgis:16-3.4
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

```sql
-- 阶段 1:实体表,幂等键在此
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

-- 阶段 1:ETL run 状态机
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

## 阶段 2.5 Spec:OSM POI 离线化

### 动机

当前每次 `/pois` 都实打 Overpass。`upstream/overpass.py` 需要在 **3 个镜像间故障转移**
(kumi.systems → overpass-api.de → openstreetmap.fr),这本身就是在线调用撑不住的证据。

离线化之后:

- `/pois` 从秒级降到毫秒级
- 3 镜像故障转移逻辑整块删除
- 解锁规模化空间 join:"为 5 万个房源各自统计 15 分钟可达范围内的设施数量"
  ——这个用 Overpass 做是不可能的
- "设施便利度评分"可以变成预计算列

### 范围

数据源:Geofabrik 的 Netherlands extract(`.osm.pbf`,约 1 GB)。

只筛当前 `/pois` 实际用到的类别(超市、学校、药店、车站、餐饮等),预计 20–50 万行。
**不要全量导入** OSM 的所有 node/way——那是另一个量级的工程。

```sql
CREATE TABLE poi (
    id         bigserial PRIMARY KEY,
    osm_type   text NOT NULL,              -- node | way | relation
    osm_id     bigint NOT NULL,
    category   text NOT NULL,              -- 归一化后的类别
    name       text,
    geom       geometry(Point, 4326) NOT NULL,
    tags       jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (osm_type, osm_id)              -- 刷新时的幂等键
);
CREATE INDEX poi_geom_gist ON poi USING GIST (geom);
CREATE INDEX poi_category_geom ON poi USING GIST (geom) INCLUDE (category);
```

### 要注意的代价

- POI 数据会过期,需要定期全量刷新(建议每月)。刷新是**幂等 upsert + 标记删除**,
  不是 `TRUNCATE` 后重灌——后者会在刷新窗口内让 `/pois` 返回空
- 首次导入是一块实在的工作量(`osm2pgsql` 或自行解析 pbf)
- 类别归一化规则要可测:OSM 的 tag 体系很脏,`shop=supermarket` 和
  `shop=convenience` 算不算同一类需要明确定义

### 为什么排在阶段 3 之前

它带来的是**真实数据产生的真实规模**,比阶段 3 造千万行假快照更有价值,
而且顺带部分解决了阶段 3 需要数据量的问题。

50 万行带 GiST 索引的表是很好的 `EXPLAIN` 练习场;每月全量刷新则是
"大表在线变更 + 分批更新"的真实载体。

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
