# 后端监控:Prometheus + Grafana,以及"上限到底是多少"

本地专用。**Vercel 上这套没有意义** —— 每次调用都是一个新进程,counter 永远不会累积,
也没有稳定的 target 可抓。

## 起停

```bash
docker compose up -d prometheus grafana postgres-exporter
```

显式点名 service 会自动激活它的 profile。三个服务都挂了 `profiles: ["monitoring"]`,
**这才是真正把它们排除在裸 `docker compose up -d` 之外的东西** —— 一句注释不行,
ors 那次已经证明过了(`docker compose config --services` 照样会列出来)。
核对方式还是同一个:

```bash
docker compose config --services     # 只应该列出 elasticsearch 和 postgres
```

| 服务 | 地址 | 作用 |
|---|---|---|
| Grafana | http://localhost:3000 | 面板,匿名 admin 免登录 |
| Prometheus | http://localhost:9090 | 抓取 + 查询 |
| postgres-exporter | 容器内 :9187 | 服务端视角的 PG 指标 |

面板是**从磁盘 provision 的**(`monitoring/grafana/dashboards/*.json`),所以它进 git、
不怕 volume 被清。反过来:**在 Grafana UI 里改的东西不会存回来**,要改就改 JSON。

## 后端为什么是"抓取"而不是"推送"

后端跑在宿主机上(`uvicorn --reload`),不在容器里,所以 Prometheus 要通过
`host.docker.internal:8000` 回连。Docker Desktop 自带这个名字;compose 里那条
`extra_hosts: host.docker.internal:host-gateway` 是为了在 Linux 上也成立。

`/metrics` **故意不在 `/api/main` 下面**。那个前缀是 `vercel.json` 路由到 serverless
函数、以及 Vite dev proxy 转发的东西;`/metrics` 是本地运维面,不该长得像公开 API 的一部分。
顺带它也就不会经过 CORS 中间件的 `allow_origins=["*"]`。

`prometheus_client` 在 `requirements-local.txt` 而不是 `requirements.txt`,所以 Vercel
的包里没有它,`/metrics` 在那边返回 **503 而不是空的 200** —— 空 200 会在图上画成一条
平的零线,看起来像"没有流量",而 503 会让 Prometheus 把 target 标成 down 并说明原因。

## 指标是挑过的,不是"因为 Prometheus 有"

| 指标 | 为什么 |
|---|---|
| `http_request_duration_seconds` | **桶是按本项目实测范围定的**(10 ms – 10 s)。库默认桶顶到 10 s,会把所有有意思的请求塞进同一个桶 |
| `http_requests_in_flight` | 实际达到的并发。"N 并发下会怎样"这个问题要求 N 可观测,而不是从压测端推断 |
| `db_pool_*` | 池是 `max_size=10`,**最可能的第一堵墙**。池耗尽从外面完全看不见:请求只是在 `acquire()` 上排队,没有错误、没有日志,只有延迟上升 |
| `event_loop_lag_seconds` | **唯一能区分"上游慢"和"这个进程饱和"的指标**。全是 async,loop 被饿死时每个单独的 await 看起来仍然很快 |
| `upstream_request_duration_seconds` | ORS 占一次 10 分钟步行请求 0.7 s 里的 0.6 s。没有这一条,延迟图没法说是我们慢还是依赖慢 |

### label 用的是路由模板,不是原始路径

`/api/main/pois?lng=4.9&lat=52.37` 当 label 会让**浏览器发过的每个坐标都变成一条时间序列** ——
无界基数,这是搞死 Prometheus 最标准的方式。所以取 `scope["route"].path`,
没匹配上任何路由的请求归到 `"unmatched"`,而不是用调用方发来的任意字符串当 label。

### 采样 gauge 为什么用后台任务而不是在 scrape 里读

池状态和 loop lag 由一个 1 秒的后台任务采,不在 `/metrics` handler 里读。
在 handler 里读意味着**它们只在 scrape 进行的那一刻被观测到**,而那恰好是池最不可能忙的时刻。

---

# 压测:`scripts/loadtest.py`

```bash
.venv/bin/python scripts/loadtest.py --scenario health      # 框架天花板(无 DB 无上游)
.venv/bin/python scripts/loadtest.py --scenario permits     # 最重的端点
.venv/bin/python scripts/loadtest.py --scenario permits --vary   # 打穿 single-flight
.venv/bin/python scripts/loadtest.py --url http://localhost:9090/-/healthy  # 对照
```

三个刻意的选择:

- **闭环、每步固定并发。** N 个 worker 各自发一个请求然后等。这量的是**服务速率**;
  开环按目标速率打,量到的是"服务端落后之后队列能涨多快",是另一回事。
- **`--vary` 会移动坐标。** `domains/geo` 在 ORS 调用前面挂了一个进程内 single-flight,
  键是 `(lng, lat, minutes, profile)`。死磕**一个**坐标量到的是去重后的路径,数字会好看;
  移动坐标量到的才是真实路径。两个都值得知道,所以它是个 flag,而不是任选其一当默认。
- **延迟从客户端这边计。** 服务端自己的直方图**不包含**请求在 uvicorn accept 队列里
  排队的时间 —— 而那恰恰是触到上限时最先出现的时间。

## `--url` 不是顺手加的功能,它是对照组

压测端自己是一个 Python 进程,**既要产生负载又要计时**。在高并发下它完全可能先成为瓶颈。
没有对照,一次吞吐塌方根本无法归因到服务端。所以 `--url` 的唯一用途是:
**拿同一个压测端去打一个已知很快的服务器**(Prometheus 的 `/-/healthy` 是 Go 写的),
看它自己的天花板在哪。

这是本项目那条规矩的又一次应用:**一次只变一个轴,而且要有一个已知良好的对照。**

---

# 实测:这个后端的上限在哪,以及是什么卡住的 (2026-10-29)

机器:Apple Silicon,8 核,Docker VM 7.7 GB。压测端 6 进程**和被测服务抢同一台机器的核**,
所以下面每个绝对值都偏低;**有意义的是轴与轴之间的比值,不是单个数字。**

## 先否定的两件事

### 1. 第一次测出来的"塌方"是压测端自己的

单进程压测端,`/health`:并发 4 时 1,184 rps,并发 128 时掉到 86 rps —— 13 倍下跌。
看起来像后端崩了。拿**同一个压测端去打 Prometheus 的 `/-/healthy`**(Go,只返回一个静态串):

| 并发 | Prometheus (Go) | FastAPI `/health` |
|---|---|---|
| 4 | **1,787** | **1,184** |
| 16 | 677 | 690 |
| 64 | 191 | 223 |
| 128 | 223 | 86 |

两条曲线在 ≥16 之后几乎重合。**一个 Go 二进制不可能在 677 rps 就撑不住,所以 ≥16 的每一行
量的都是压测端。** 改成 6 进程后压测端峰值从 1,787 抬到 **5,703 rps**(3.2 倍),这才是个
能用的仪器。

> 教训:**压测端是测量仪器,仪器本身要先标定。** 没有对照组,一次塌方无法归因。

### 2. "loop lag 只有 1 ms"也是错的 —— 我在压测结束后量的

压测**结束后**读 `/metrics`:`event_loop_lag_seconds 0.0011`。据此我差点得出"Python 进程
没饱和"。在压测**过程中**连续采样 18 次:**0.002 – 0.537 秒**,典型值 0.05–0.23 s。

> 教训:**饱和指标必须在负载中采,事后读到的是空闲值。** 这和"200 不是正确答案的证据"
> 是同一类错误 —— 读数本身没问题,读的时刻不对。
>
> 附带:我那个采样脚本的 `pool_used` 列是坏的(三次独立 curl,`size` 和 `idle` 来自不同次
> 抓取,相减无意义)。那一列作废,没有用它下任何结论。

## 四个轴,一次只变一个

基线 = 你现在的开发配置(`uvicorn --reload`,1 worker,pool 10)。

| 轴 | `/bekendmakingen` 峰值 | 变化 |
|---|---|---|
| **基线**(`--reload`,1 worker,pool 10) | **33.2 req/s** @并发16 | — |
| 关掉 `--reload` | 40.3 req/s @并发32 | **+21%** |
| pool 10 → 30 | 38.7 req/s | **持平/略降 — 池不是墙** |
| 1 worker → **4 workers** | **72.6 req/s** @并发32 | **+80%** |
| 4 workers + 坐标变化(真实场景) | **58.1 req/s** | −20% |

`/health`(无 DB 无上游)作为框架天花板:

| 配置 | 峰值 |
|---|---|
| `--reload`,1 worker | 2,634 rps (p95 8 ms) |
| 无 `--reload`,1 worker | **3,078 rps** (p95 7 ms) |
| 无 `--reload`,4 workers | 2,840 rps |

**4 worker 让重端点快 1.8 倍,却让 `/health` 略微变慢。** 因为 `/health` 根本没把任何一个
核跑满,多进程只带来竞争。**这正是"加 worker"不能当成通用建议的原因。**

## 所以墙是什么

逐个排除,每条都有数:

| 嫌疑 | 证据 | 结论 |
|---|---|---|
| ORS 上游 | 固定坐标下 **0.18 次调用/请求**(single-flight 去重掉 82%),占请求时间 **4%** | ❌ 不是 |
| 连接池 | 10 → 30,吞吐**没动** | ❌ 不是 |
| Postgres | 容器 CPU 1.3–4.3 核(VM 共 8 核),多数时刻只有 1 个 active 查询 | ❌ 不是 |
| 单进程 Python CPU | 压测中单 worker 稳定 **71–96% 的一个核**;loop lag 0.05–0.54 s | ✅ 是,但这是**症状** |
| **每次调用新建 `httpx.AsyncClient`** | 构造一次 **13.88 ms CPU**;修掉之后 **22.3 → 7.5 ms/请求** | ✅ **这才是原因** |

### ⚠️ 本节第一版的结论是错的,订正如下

第一版写的是:「每个请求要解析 GeoJSON → `json.dumps` → 解码 jsonb → 序列化,**这些累积
起来**就是 CPU 墙」。**数字不支持这个说法**,而我当时没有去量它们,只是列举了一串看起来
很忙的操作。实测:

| 阶段 | CPU |
|---|---|
| DB 往返(含 asyncpg 解码 284 条) | 0.9 ms |
| `jsonable_encoder` + `json.dumps` 108 KB | 3.5 ms |
| 两条 JSON 日志 | 0.03 ms |
| **实测每请求总计** | **22.3 ms** |

差了 18 ms。**单轴验证**:同一端点只改时间窗口,把返回条数从 284 砍到 2 条:

| 窗口 | items | 响应 | rps | CPU/请求 |
|---|---|---|---|---|
| 12 个月 | 284 | 102 KB | 40.9 | 22.3 ms |
| 1 个月 | 43 | 16 KB | 54.9 | 17.9 ms |
| 1 天 | **2** | **1.1 KB** | 57.9 | **17.1 ms** |

响应体只解释 5.2 ms。**返回 2 条、1.1 KB 的请求仍然要烧 17.1 ms**,而 `/health` 走同一套
中间件只要 0.32 ms —— 所以既不是中间件,也不是"干的活多"。

### 真正的原因:per-call 构造 `httpx.AsyncClient`

```
kv_get 一次(必然失败):        墙钟 17.03 ms | CPU 14.53 ms
只是 new httpx.AsyncClient(): CPU 13.88 ms       <- 95% 在这里
```

构造一个 AsyncClient 要从系统信任库重建 TLS context。代码里**有 10 处**是
`async with httpx.AsyncClient(...) as client:`(cache 2、ors 2、tomtom 2、search 2、
overpass 1、pdok 1)。而 Upstash 的域名自 2026-10-28 起 NXDOMAIN,于是**每个请求都要为一次
注定失败的缓存查询付 14 ms**。

修法是 `api/main/http_client.py`:一个进程级共享实例,`timeout` / `headers` 仍按调用点传。

| 配置 | CPU/请求 | rps | p95 |
|---|---|---|---|
| per-call 构造(原状) | 22.3 ms | 40.9 | 1,495 ms |
| 关掉死缓存(**只是诊断手段**) | 7.4 ms | 107.7 | 452 ms |
| **共享 client(真修复)** | **7.5 ms** | **119.4** | **364 ms** |

共享 client 在**缓存照常配置、照常失败、日志里照常 3,608 次 `kv get failed`** 的前提下拿回
了全部损失 —— 这个计数是用来证明对照有效的,不是装饰。

> **"加 worker" 是治标。** 它确实有用(+80%),但它只是给你更多核去烧同一笔浪费。
> 先修掉浪费,2.9 倍;再谈要不要加 worker。

每个 `/bekendmakingen` 请求在 Python 里确实也做解析和序列化,但那只有 4.5 ms。

## 结论,按可操作性排序

1. **容量约是 40–70 req/s(重端点)、约 3,000 req/s(平凡端点),差 75 倍。** 谈"后端上限"
   时必须说是哪个端点,否则数字没有意义。
2. **要提容量就加 worker,不是加池。** 池已经是过量供给的。
3. **真实场景比固定坐标低 20%**,因为 single-flight 去重失效,ORS 占比从 4% 升到 26%,
   ORS 的平均响应也从 87 ms 涨到 173 ms(它自己开始排队了)。
4. 生产在 Vercel 上,**以上没有一条适用于那里** —— 那是按请求扩容的无状态函数,
   没有常驻进程、没有连接池复用、也没有 single-flight。这些数字描述的是本地这套栈。

## Removing the dead Upstash cache (2026-10-29)

The Upstash host has been NXDOMAIN since 2026-10-28, so `kv_get`/`kv_set` threw on every
call and the cache could never hit. `UPSTASH_REDIS_REST_URL`/`_TOKEN` are now commented out
in `.env`, which makes `cache.py` short-circuit on `if not UPSTASH_URL or not UPSTASH_TOKEN`
before any network call. `.env` is gitignored, so this is a local change only — **Vercel's
own env vars are untouched and production still has the dead instance configured.**

Measured back to back, two uvicorn processes on the same machine, same 30 s / conc 32 /
`--vary` run, the only difference being whether the two variables are set:

| | CPU per request | rps | p50 | p95 | one core busy |
|---|---|---|---|---|---|
| dead cache configured | 7.27 ms | 115.3 | 245 ms | 512 ms | 84% |
| cache removed | **5.50 ms** | 115.7 | 214 ms | 523 ms | **64%** |

**CPU per request fell 24%; throughput did not move at all.** That is the result, and it is
not a disappointment — it is the binding constraint changing hands. Before the shared-client
fix the endpoint spent 22.3 ms of CPU at 40.9 rps, i.e. 91% of the one core a single worker
can use: it was at the CPU wall, and every millisecond saved was a millisecond of throughput.
After it, 115 rps × 5.5 ms is 64% of that core, so the process is idle a third of the time and
is waiting on something else — ORS, since `--vary` makes every request a unique isochrone that
cannot be deduplicated by the single-flight map.

So the honest claim is **headroom, not speed**: the same box now serves the same traffic with
a quarter less CPU, which is what a second worker or a busier endpoint would have spent.
Do not quote this as a throughput win.

### A load generator that invented 2736 rps

Found while taking the measurement above. `--base http://127.0.0.1:8002` replaced the whole
of `BASE`, which carries the `/api/main` prefix, so every scenario requested
`http://127.0.0.1:8002/bekendmakingen` — a 404. `_worker` counted `status_code < 500` as a
success, so 82,110 consecutive 404s were reported as `ok 82110  err 0` at **2736 rps and
p95 19 ms**, about 8x the real ceiling of the endpoint it claimed to be measuring, at
0.35 ms of CPU per request.

Two separate defects, and the dangerous one is the status threshold: a wrong `--base` is a
typo that announces itself the moment anything checks, while `< 500` is a generator that
cannot distinguish "served" from "routed nowhere" and will quietly fabricate capacity from
any mistake that produces a 4xx. Both are fixed — `< 400`, and `--base` now appends the
prefix when it is missing.

The tell was the same one as always: 0.35 ms of CPU per request is `/health` territory, and
`/bekendmakingen` runs an ORS call and a GiST scan. **A throughput number with no plausible
cost per request behind it is not a measurement.** This is the third time in this work that
a 2xx-or-near-2xx status was treated as evidence of the right work being done.
