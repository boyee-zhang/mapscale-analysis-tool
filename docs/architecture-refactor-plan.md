# 结构重构计划

分支:`refactor/modular-architecture`(截至撰写时仅有 1 个 commit `afed2d2`,只改了 CLAUDE.md,代码未动)

配套文档:`docs/postgres-refactor-plan.md`。两份计划有依赖关系,见「与 Postgres 计划的交错」。

## 现状规模

源码约 5000 行:后端 `api/` 约 2300 行,前端 `frontend/src/` 约 2700 行。
这个体量很小,所以本计划刻意避免大爆炸式重组——**每个阶段都是能独立合入、不改变外部行为的小步**。

## 根因与对应处置

| 根因 | 本次处置 |
|---|---|
| **RC1** 分层按上游 API 而非业务能力(`routers/` 和 `clients/` 都是上游形状) | 阶段 A–C。引入 `domains/`,`clients/` 降级为纯传输层 `upstream/` |
| **RC2** repo 边界按语言/部署而非领域 | **改为只迁 CBS ETL,不合并 repo**。见下方「被否决的方案」 |
| **RC3** 没有 AI 层,LLM 调用在 4 处就地生长 | 阶段 C 附带收口;不单独立项 |
| **RC4** 前端没有状态层,`MapContainer` 就是 store | **本次不做**。见「推迟的部分」 |

## 关键约束:不改 URL,不改部署配置

当前 `api/main/` 是包根,全部 18 个端点带 `/api/main` 前缀,并被三处硬编码依赖:

- `vercel.json` — `/api/main/:path*` → `/api/main/index`
- `frontend/vite.config.js` — `/api/main/*` → `localhost:8000`
- `frontend/src/api.js` — 所有请求路径

把 `api/main/` 改名成 `api/` 会连带改掉这三处加 18 个路由前缀,纯粹是无收益的 churn。

**决定:`api/main/` 作为包根保持不变,所有重组发生在它内部。**

验收时整个公开 API 表面必须零变化——这由现有的 `tests/integration` 直接验证。

## 目标布局

```
api/main/
  app.py            FastAPI 实例、中间件、异常处理、路由注册
  index.py          Vercel 入口(re-export app)
  config.py         env + MODE_MAPPING + SPEED_MAP
  errors.py         ExternalServiceError / InternalError
  logger.py         JSON 日志 + request-id 中间件
  cache.py          Redis TTL 缓存
  db.py             asyncpg pool(由 Postgres 计划阶段 0 引入)

  upstream/         纯传输层。HTTP 调用 + 错误翻译,仅此而已
    ors.py  overpass.py  pdok.py  tomtom.py  es.py  nominatim.py

  domains/
    geo/            「这个点周边/可达范围内有什么」。原稿叫 spatial/,见下方「命名决定」
      service.py    isochrone() / get_pois()(已建);route() / region() / traffic 待迁
                    路由仍留在 routers/isochrone.py + pois.py,是薄调用层
    search/
      router.py     /search
      service.py    ES → PDOK → Nominatim 瀑布 + 回写
    housing/
      router.py     /housing/*
      service.py    用例编排
      repo.py       SQL 全部集中在此(Postgres 计划引入)
      models.py registry.py providers/ geocoder.py etl.py stats.py
      chat_agent.py monitor.py boundaries.py
    regions/        阶段 D:从 Node 迁来的 CBS ETL
```

### `upstream/` 的硬规则

`upstream/` 里的函数**不得**包含业务规则、缓存决策或领域类型。

> 判据:如果 `upstream/` 里的某个函数需要知道什么是 "listing"、什么是 "isochrone",它就放错了。

这条规则是 RC1 的实际解法。`clients/` 当前的问题不是"按上游组织"本身——传输层按上游组织
是正确的——而是**没有领域层,所以业务语义无处可去,只能渗进 clients 和 routers**。
加上 `domains/` 之后,按上游组织的传输层就变得合理了。

PDOK 是典型案例:它一个上游服务承担 4 种语义(反向地理编码、Locatieserver 自由搜索、
地址转坐标、CBS WFS 边界)。处置是 `upstream/pdok.py` 只提供 4 个薄 HTTP 函数,
各自的语义分别归 `geo` / `search` / `housing` 三个领域的 service。

### 命名决定:`spatial/` → `geo/`(2026-10-18 改名,原稿全文已随之更新)

本文原稿全程写 `domains/spatial/`,但 2026-10-18 实际落地时建的是
`api/main/domains/geo/`。**改为以代码为准**,全文统一成 `geo/`。

改名原因,按权重排列:

1. **文档对不上代码是比名字不够精确更贵的错误。** 计划文档的唯一作用是被下一次开发读,
   一份指向不存在目录的计划会让人以为阶段 B 没动过。
2. 既有词汇一致。`/isochrone`、`/pois`、`SPEED_MAP`、前端 `geo` 相关调用,以及
   CLAUDE.md 的 "geo analysis" 子系统说法,都已经用 geo 这个词;`spatial` 是本文独有的新词。
3. `geo` 更短,且 `from ..geo import service as geo` 不需要起别名。

唯一反对理由记录在此,以免日后重复讨论:**`geo` 与 geocoding 有歧义**
(`domains/housing/geocoder.py` 做的是地址→坐标,和本域无关)。判定为可接受——
地理编码一直归 `housing`(它的调用方和缓存表都在那),本域处理的是"已知坐标的周边与可达性",
两者在代码里不相邻,不会真的混淆。

改名成本为零的时间窗就是现在:`api/main/domains/geo/` 还未 commit 进 git,
真要反向改回 `spatial/` 也只是一次 `git mv`。过了阶段 B 就不值得再动。

### `geo` 为什么是底座

它是其他领域组合的**空间原语层**,不是平级的功能模块。

落地检验:阶段 B 完成后,`housing` 要实现"房源周边 15 分钟可达什么"时,必须
`from ..geo import service as geo`,而不是自己再写一遍 isochrone 调用。

`traffic` 并入 `geo` 而非独立成域——它是地图叠加数据,不是独立业务能力,
且 `/traffic/flow` 目前无任何调用方(见 CLAUDE.md 已知问题)。

---

## 阶段

### 阶段 A:目录骨架 + housing 归位(Postgres 计划的前置)

`housing/` 当前已经是全仓库结构最好的部分(models / registry / providers 的 provider 无关设计)。
本阶段只是把它挪到 `domains/` 下并建立骨架。

1. `git mv api/main/clients api/main/upstream`
2. 建 `api/main/domains/__init__.py`
3. `git mv api/main/housing api/main/domains/housing`
4. `git mv api/main/routers/housing.py api/main/domains/housing/router.py`
5. 修 import;`app.py` 的路由注册改为从 domain 导入

**纯移动,零行为变更。** 必须是独立 commit,不夹带任何逻辑修改。

验收:

- `ruff check api/` 通过
- `pytest tests/unit` 通过
- `BASE_URL=http://localhost:8000 pytest` 通过,18 个端点响应不变
- `git log --follow` 对移动后的文件仍能追溯历史

> 阶段 A 必须在 Postgres 计划阶段 0 之前完成,否则 `geocoder.py` / `boundaries.py` 改完还要再移一次。

### 阶段 B:抽出 geo 领域(**部分已完成,进度见下**)

把 `routers/isochrone.py` `pois.py` `directions.py` `region.py` `traffic.py`(合计约 220 行)
收进 `domains/geo/`,并**把业务逻辑从 router 下沉到 service**。

当前 router 里混着的东西要分开:

- 参数校验 + 响应序列化 → 留在 `router.py`
- `MODE_MAPPING` / `SPEED_MAP` 查表、由速度推导 Overpass 半径、缓存键构造 → 下沉到 `service.py`
- HTTP 调用 → 留在 `upstream/`

#### 进度(2026-10-18)

| 端点 | 状态 |
|---|---|
| `/isochrone` | ✅ 逻辑在 `domains/geo/service.py`,`routers/isochrone.py` 是薄调用层 |
| `/pois` | ✅ 同上,且已切到 Postgres `ST_Contains` |
| `/directions` `/bulk_directions` | ⬜ 仍是原 router |
| `/region` | ⬜ 仍是原 router |
| `/traffic/*` | ⬜ 仍是原 router |

**为什么这两个先做、且提前于计划:** 不是挑简单的做,而是 Postgres 阶段 1 的 `/pois`
**必须**读 `/isochrone` 刚写进缓存的那个多边形,两者得共享缓存键
(`cache.geo_cache_key()`,原先 `iso:` 用 `.5f`、`pois:` 用 `.3f`,共享读会 100% miss)。
跨 router 共享私有状态是做不到的,所以"先建 service 再接 Postgres"是硬顺序,不是偏好。

`routers/` 目录因此**还没有消失**——`directions` / `region` / `traffic` / `search` / `health`
五个文件还在。验收条款里"`routers/` 目录消失"要等阶段 C 一起达成。

#### `points_in_polygon()`:原计划被跳过,直接走了终态

原稿写"本阶段先在后端提供等价实现,前端暂不切换,实际切到 PostGIS `ST_Contains` 是
Postgres 计划阶段 4"。**实际执行是 2026-10-18 一步到位切了 PostGIS**,前端
`MapContainer.jsx` 的 Turf `booleanPointInPolygon` `useMemo` 已删除。

跳过中间态的原因:原计划的"后端等价实现"是在 Python 里再写一遍点面判断,
而 `/pois` 的数据此时已经在 PostGIS 里了——写一个注定要删的 Python 实现,
唯一产出是一次多余的 review 和一次多余的删除。中间态只在"前端还要继续用一段"时才有价值,
而这里前端是同一个 commit 里切的。

副作用:`@turf/turf` 现在没有任何地方 import,但仍在 `package.json` 里(见已知问题)。

顺手修掉两个已知 live bug(它们正好在本阶段触及的代码里):

- `AnalysisPanel.jsx:35` 发送 `"driving-car"`,而 `MODE_MAPPING`/`SPEED_MAP` 的键是 `"driving"`
  → 静默回退到步行 + 80 m/min。**driving 模式目前是坏的。**
- `MapContainer.jsx:46` 的 `const [, setHoveredRoute] = useState(null)` 丢弃了值,
  导致每次 POI hover 都白打一次 ORS `/directions`。

验收:公开端点行为不变;`routers/` 目录消失;`service.py` 不 import `fastapi`。

> 最后一条是分层是否真的成立的硬判据——service 层若依赖 web 框架,说明逻辑没下沉干净。

### 阶段 C:search 领域 + 并入 lab

1. `routers/search.py`(233 行)→ `domains/search/`,瀑布逻辑下沉到 service
2. 从未合并分支 `feature/search-index-lab`(单 commit `f84aae0`)**采摘**两样东西并入生产 `/search`:
   - edge n-gram 自动补全
   - 高斯距离衰减打分
3. `/search_lab` 端点和 `osm_places_lab` 索引**不要**带进来。该分支本身不合并。
4. DeepSeek 调用点收口:当前散在 `stats.py` / `chat_agent.py` / `monitor.py` 三处(RC3)。
   本阶段只做最小收口——抽一个共用的 client 到 `upstream/deepseek.py`,
   prompt 仍留在各自领域。**不建"AI 层"**,本次迭代 AI 不是重点。

验收:`/search` 对现有查询的结果不劣化(需要先留一组基准查询及其当前结果作为对照)。

> 注意:这是全计划中唯一会改变外部行为的阶段(打分变了)。所以要先有基准。

### 阶段 D:regions 领域(CBS ETL 迁入 Python)

从 `mapscale-ai-service` 迁入的范围——**按「有状态/无状态」切,不是整体搬**:

| 文件 | 行为 | 处置 |
|---|---|---|
| `lib/scraper.js` | 拉 CBS OData | **迁** → `domains/regions/` |
| `lib/transformers/{housing,population,safety,traffic}.js` | 4 个转换器 | **迁** |
| `lib/etl/sync_traffic.js` | 月度流量同步 | **迁** |
| `lib/kv.js` | Redis 读写 | **丢弃**,改为写 Postgres |
| `api/analyze.js` | prompt + DeepSeek 调用 | **不迁**,留在 Node |
| `lib/benchmarks.js` | 基准数据 | **不迁** |

迁移动机是数据层统一:这些 ETL 把结果写成 Redis 的 `raw:*` / `features:*`,**无 TTL、永久堆积、
无法做历史对比**。进 Postgres 后才能真正做时序分析。

Node 侧保留的 `POST /api/analyze` 改为从 Python 读数据(或直接查 Postgres 只读),
继续通过 `/api/ai/*` 代理,部署方式不变。

同时处理 ai-service 的 node_modules:`git rm -r --cached node_modules` + 确认 `.gitignore` 生效。
**历史不重写**——既然不合并 repo,1366 个文件留在历史里只是体积问题,不值得 `git-filter-repo` 的风险。

遗留问题(迁移时顺手修或至少记录):当前 traffic 数据是 **PV27(北荷兰省)的省级代理值,
被套用到全部约 340 个市**。其他省份的结果不具备地域特异性。这是数据正确性问题,不是结构问题。

---

## 与 Postgres 计划的交错

两份计划动同一批文件,顺序不能乱。**PG 的"阶段 1"在 2026-10-17 换了内容**:原本是 housing ETL,
被 OSM POI 离线化占用,housing ETL 则作为 `1'` 搁置(provider 全部不可达)。**其余阶段号没有顺延**
——以 `docs/postgres-refactor-plan.md` 的「阶段划分」表为准:

```
阶段 A(目录骨架 + housing 归位)                       ✅ 完成 fbe2e08
   ↓  必须先完成,否则 geocoder/boundaries 要移两次
PG 阶段 0(PostGIS + geocode_cache + municipality)     ✅ 完成 1941317
PG 阶段 1(OSM POI 离线化 + /pois 切换 + etl_run)      ✅ 完成 2026-10-18,未 commit
PG 阶段 1'(housing ETL)                               ⛔ 搁置:provider 全部不可达
阶段 B(geo 抽取)                                      🔶 isochrone/pois 已做,余 3 个端点未动
PG 阶段 2(outbox + SKIP LOCKED worker)                ⛔ 需迁出 Vercel(worker 要常驻)+ 见下
PG 阶段 3(合成快照 + 按 captured_at 分区 + 调优)      ⛔ 见下方阻塞
PG 阶段 4(给 housing_listing 加 geom + 在线建索引)    ⛔ 见下方阻塞
阶段 C(search + lab 并入)
阶段 D(regions ETL 迁入)
PG 阶段 5(PgBouncer + 复制)                           ⬜ 本次不做
```

**只有阶段 A 是硬前置。** 阶段 B/C/D 都可以推迟甚至不做,不阻塞 Postgres 工作。

> 原稿这里有一行"阶段 B 的 points_in_polygon 切到 PostGIS;前端去掉 Turf 过滤",已删除:
> 它在 PG 阶段 1 就连同 `/pois` 一起做完了,见阶段 B 小节。

### ⛔ PG 阶段 2/3/4 共享同一个阻塞:`housing_listing` 表不存在

这三个阶段的产物全部挂在 `housing_listing` 上:阶段 2 的 outbox 消费的是 listing 事件,
阶段 3 的 `housing_listing_snapshot` 外键指向它并要灌到 1000–2000 万行,
阶段 4 就是给它加 `geom` 列再在线回填。而这张表至今没建,因为
**所有荷兰商业房源站都在 Cloudflare managed challenge 后面,没有任何 provider 能取到行**
(见 `docs/data-source-feasibility.md`),housing ETL 本身已作为 `1'` 搁置。

注意区分两件事,否则容易得出错误结论:

- **练习内容不被阻塞。** 幂等 upsert、`etl_run` 状态机、写偏斜、部分唯一索引、
  sentinel 校验,都与"谁提供行"无关——这一点在 CLAUDE.md 里已经纠正过一次,
  且 `etl_run` 确实已经在 PG 阶段 1 里用 POI 导入跑通了。
- **但数据被阻塞。** 没有真实行就没有真实的每日快照,而**历史是唯一事后补不回来的东西**:
  每拖一天,就永久少一天的 listing 历史。所以不能用"等 provider 通了再说"来处理。

可选出路(尚未决策,四选一):

1. 把 PG 阶段 3 的合成历史改到 `poi` 上做——`poi` 已有 82k 真实行,分区/调优照样能练,
   但练不到 upsert 冲突与 sentinel。
2. 用 BAG 构造 `housing_listing`(PDOK WFS 有 9.99M 真实住宅),拿真实行跑完整 ETL,
   代价是它不是"广告"语义,时间序列是假的。
3. 先把后端迁出 Vercel,直接做 PG 阶段 2 的 worker——它需要常驻进程,这件事早晚要做。
4. 暂时放下 Postgres,转去做架构阶段 B/C(把剩下 5 个 router 下沉)。

考虑到本次迭代的重心在 Postgres,合理的最小路径是:**阶段 A → PG 0/1 →(解除上述阻塞)→
PG 2/3/4 → 视剩余精力做 B。**

---

## 被否决的方案

### monorepo 合并(原决策,现撤销)

原论据:两种语言写同一组 Postgres 表会导致两套 migration 工具,或一方拥有 migration
另一方静默漂移。

**撤销理由:该论据已被阶段 D 的决定消解。** CBS ETL 迁进 Python 后,Node 侧只剩无状态推理
(prompt + DeepSeek 调用),永不接触数据库。一个无状态 HTTP 服务住在哪个 repo 无关紧要。

代价则很具体:ai-service 有 1366 个 node_modules 文件进了 git 历史,合并前需
`git-filter-repo` 重写历史。为一个已消失的理由承担重写历史的风险,不划算。

**结论:两个 repo 保持独立,无限期推迟合并。** 若将来 Node 侧确实需要直连 Postgres,重新评估。

### 把 `api/main/` 改名为 `api/`

牵连 `vercel.json`、`vite.config.js`、`api.js` 和 18 个路由前缀,无任何收益。

---

## 推迟的部分

### RC4:前端状态层

`MapContainer.jsx`(295 行)同时是容器、store 和 fetch 编排。`HousingChatPanel`(369 行)、
`AnalysisResultPanel`(351 行)、`HousingLayer`(302 行)都偏大。

推迟的理由是坦率的:**与本次迭代的 Postgres 重心无关,且不服务学习目标。**
前端目前能用,组件过大是可读性问题而非正确性问题。

唯一例外是阶段 B 里顺手修的两个 live bug——它们是正确性问题。

### 把 Node 推理服务重写为 Python

无状态、不碰数据库、能跑。没有重写的技术理由。想退役随时可退役,不占本次工作量。

### 测试覆盖

当前 housing 子系统、DeepSeek agents、cron token 校验、所有错误路径(502/504)**均无测试**。

本计划不单独立项补测试,但有一条强制要求:**阶段 A/B 作为纯移动,必须靠现有
`tests/integration` 验证行为不变。** 若某端点因无测试而无法验证,就在移动它之前先补一个
最小的冒烟测试。这是把测试债还在最需要的地方,而不是泛泛地追覆盖率。
