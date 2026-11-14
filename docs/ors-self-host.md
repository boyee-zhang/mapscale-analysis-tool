# 自建 OpenRouteService:可行性与 runbook

状态:**已构建并切换(2026-10-28)。** 三张图 211.79 s 建完,峰值堆 2.03 GB,
本地 `.env` 的 `ORS_BASE_URL` 已指向自建实例并端到端验证。
构建仍留给人手动触发(`profiles: ["ors"]`)。service 上挂了 `profiles: ["ors"]`,
这才是真正把它排除在裸 `docker compose up -d` 之外的东西 —— 原先只写了一句注释,而
`docker compose config --services` 照样把 ors 列出来,也就是裸 `up -d` 会直接触发建图。
显式点名 service 会自动启用它的 profile,所以下面的命令不受影响。

## 为什么要自建(两个都不是我们能修的)

1. **免费额度是 50 次/天/端点**,实测自 `x-ratelimit-limit: 50`;`x-ratelimit-reset` 在
   22.6 h 之后,所以窗口是天,不是分钟。2026-10-18 一天内:我自己排查 profile 故障的
   探测用掉 30+ 次,用户 18 次地图交互在 2.1x 放大下用掉 38 次,于是 `/isochrone` 整天
   503/403/502。**额度耗尽时 Tyk 网关会交替返回 403 `Quota exceeded`、429 和 nginx 502
   HTML——三个状态码,一个原因。**
2. **公网 `foot-walking` 直接黑洞**,`cycling-*` 则返回 HTTP 200 但等时圈约小 400 倍。
   证据见 `pg-notes/ors-profile-outage.md`。
   ⚠️ **这一条原本写的是"自建是从我们自己的 pbf 重建图,所以这两个故障按定义不会带过来"
   —— 实测后只对了一半,见下面「构建后的实测结果」。** `foot-walking` 确实自建就好了;
   cycling 的小圈在自建上原样复现,而再换一个坐标就发现它根本不是图的问题。
   也就是说:自建的真实收益是**额度**和**可观测性**,不是"修好两个 profile"。

缓存只能救重访;每个新中心点都是一次新调用。50/天意味着这个功能在免费层**没法演示**,
所以自建是结构性答案,不是优化。

## 前置条件(全部已核实,2026-10-18)

| 条件 | 实测 |
|---|---|
| OSM extract | `data/noord-holland-latest.osm.pbf` 189.8 MB,已在本地 |
| 镜像架构 | `openrouteservice/openrouteservice` manifest 含 **linux/arm64** |
| Docker 资源 | VM 8.2 GB RAM / 8 CPU;磁盘可用 58 GB |
| 配置键名 | v10.1.0 `application.yml`:`ors.engine.profile_default.build.source_file`、`ors.engine.profiles.<name>.enabled`;context-path `/ors`,端口 8082 |

镜像是多架构的,所以**不会重演 `postgis/postgis` 的坑**——那个只发 amd64,在 Apple
Silicon 上走模拟,把每个 `EXPLAIN ANALYZE` 的计时都变成废数据。

### 一个 pbf,两个消费者
`etl/ingest/poi.py` 读 POI 标签的 node/way(`shop=`/`amenity=`);ORS 读 `highway=*`
并按 profile 构建 contraction hierarchy。同一个文件,互不干扰,所以挂成 `:ro`。

## 代码改动面(已完成)

- `config.py`:新增 `ORS_BASE_URL`,默认 `https://api.openrouteservice.org/v2`,
  **默认值等于现状,所以 Vercel 行为不变**。
- `upstream/ors.py`:两处硬编码 URL(isochrones / directions)改用该变量。
- `docker-compose.yml`:新增 `ors` service + `ors_graphs` / `ors_elevation` volume。

变量包含 `/v2`,因为三个部署的前缀不一致:

```
https://api.openrouteservice.org/v2          现在用的遗留公网地址(官方 spec 里没有)
https://api.heigit.org/openrouteservice/v2   官方 OpenAPI spec 的 servers[0]
http://localhost:8080/ors/v2                 自建容器
```

顺带把「要不要迁到 heigit」从一次代码改动降级成一个环境变量。自建实例完全不校验
key,所以 `Authorization` 头保持无条件发送即可。

## 构建(人工跑)

```bash
docker compose up -d ors                 # 首次启动即构建三张图
docker compose logs -f ors               # 盯 "Building graph" / OOM
curl -s localhost:8080/ors/v2/health     # 期望 {"status":"ready"}
curl -s localhost:8080/ors/v2/status | python3 -m json.tool   # 哪些图真的加载了
```

`/v2/health` 和 `/v2/status` 在公网网关上是 404(spec 里有,只对自建开放),所以
「现在到底哪些 profile 可用」这个问题第一次变得可以直接问。

切过去:

```bash
echo 'ORS_BASE_URL=http://localhost:8080/ors/v2' >> .env
# 重启 uvicorn
```

## 构建后的实测结果(2026-10-28)

原先列了三件必测的事,下面是答案。面积一律在 PostGIS 里用
`ST_Area(geom::geography)/1e6` 从返回的几何重算,不采信 ORS 自报的 `area`(两者差 <0.2%,
所以那个属性可信——但这现在是量出来的结论,不是假设)。

1. **`cycling-regular` 的面积 / reachfactor。** 在公网那个测试点 `(4.9003, 52.3791)` 上自建
   给出 **0.0308 km² / 0.0016**,和公网几乎逐位相同。但把坐标挪 200 m 就变成 10–15 km² /
   reachfactor 0.74–0.77,Haarlem 14.87、Alkmaar 16.08、Hoorn 12.45 —— 正是原始笔记自己
   预测的健康值。**所以"四张 cycling 图都坏了"是一次单点测量的产物:那个坐标吸附到一个
   自行车走不出去的孤岛上(`/directions` 报 ORS code 2009,而 `foot-walking` 同样 731 m
   走得通)。** 完整表格在 `pg-notes/ors-profile-outage.md` 末尾。
   Known Issue 因此改写而不是撤销:**400 倍偏小的多边形依然以 HTTP 200 到达**,我们依然
   看不见,该补的仍是那个合理性校验。
   公网是否在别的坐标上也正常 **没测到** —— 预算给它的那一次请求撞上
   `403 {"error": "Quota exceeded"}`,额度当天已用尽。未验证,不是已确认。
2. **`foot-walking` 是否还黑洞。** 自建上**完全正常**:1.489 km² / reachfactor 1.0,与
   `foot-hiking` 到小数第八位相同,`/directions` 731 m / 526 s。故障在**公网部署**里,
   不在 ORS 也不在图里。
   原先这条写着"若正常,`MODE_MAPPING["walking"]` 应改回 `foot-walking`"——**这个结论是错的**:
   那是一个全局常量,而 Vercel 上的生产环境打的是公网 API,改回去等于用修好 localhost
   的代价弄坏生产。保持 `foot-hiking`。
3. **构建实际吃了多少。** 三张图顺序构建共 **211.79 s**(foot-hiking 121 s + driving-car
   34 s + cycling-regular 56 s),峰值堆 **2.03 GB / 4 GB**,容器峰值 RSS **2987 MiB**。
   `XMX: 4g` 保留不下调:这是单个 extract 的数字,余量正是换更大 extract 时要用的。
   热启动(图已在 volume 里)约 20 s 到 healthy。

## healthcheck 本身也错了两次(都值得记)

1. **镜像里没有 `curl`,只有 `wget`。** 第一版 healthcheck 用 curl,于是容器在图建好、
   服务正常的情况下被报了一个小时的 `unhealthy`,`docker inspect` 里 120 次连续失败全是
   `/bin/sh: curl: not found`。**一个跑不起来的探针和一个挂掉的服务长得一模一样**,所以
   要看探针自己的输出,不是只看它的结论。(我差点因此以为建图失败——实际日志里
   `Total time: 211.79s` 早就写着建完了。)
2. **`/v2/health` 不代表图加载完。** 它在启动 20 s 后就返回 `{"status":"ready"}`,而
   `/v2/status` 当时的 `profiles` 还是空的,图又过了 40 s 才加载完。`ready` 只说明 HTTP
   层起来了。这和"公网返回 200 就以为答案对"是同一个错。现在的 healthcheck 断言
   `/v2/status` 里出现三个 profile 名,这才是真能抓到它的检查。

## 已知代价

- **覆盖范围从全球缩到北荷兰一省。** ⚠️ **2026-10-28 更正:这条原本写的是"恰好与 `poi` 表
  (82,259 行,仅 NH)一致,所以消除了一处不一致"——该前提已失效。** PG 阶段 1 在 2026-10-18
  导入了全国,`poi` 现有 512,844 行,bbox 3.36..7.22 lng / 50.73..53.50 lat。所以自建**引入**
  了一处不一致:北荷兰以外有 POI 但没有等时线,`/pois` 在那些地方退回测地圆。
  用全国 extract 不是选项:ORS 建图大约需要 extract 体积的 8-10 倍堆内存,
  `netherlands-latest.osm.pbf`(1.3 GB)要 10-13 GB,而本机 Docker VM 只有 8.2 GB。
  这是动手前量的,不是 OOM 之后才知道的。
- **Vercel 上没有自建实例。** 生产仍走公网 50/天。真正解决要把后端搬离 Vercel——
  这和 PG 阶段 2 的 `SKIP LOCKED` worker 需要常驻进程是同一个前提。
- 图数据放在 docker volume 里,不进 git。换 major 版本要重建。
