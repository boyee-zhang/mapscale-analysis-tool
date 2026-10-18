# 自建 OpenRouteService:可行性与 runbook

状态:**代码侧已就绪(`ORS_BASE_URL` + compose `ors` service),图未构建。**
构建要跑几十分钟、占几 GB 内存,所以留给人手动触发。service 上挂了 `profiles: ["ors"]`,
这才是真正把它排除在裸 `docker compose up -d` 之外的东西 —— 原先只写了一句注释,而
`docker compose config --services` 照样把 ors 列出来,也就是裸 `up -d` 会直接触发建图。
显式点名 service 会自动启用它的 profile,所以下面的命令不受影响。

## 为什么要自建(两个都不是我们能修的)

1. **免费额度是 50 次/天/端点**,实测自 `x-ratelimit-limit: 50`;`x-ratelimit-reset` 在
   22.6 h 之后,所以窗口是天,不是分钟。2026-10-18 一天内:我自己排查 profile 故障的
   探测用掉 30+ 次,用户 18 次地图交互在 2.1x 放大下用掉 38 次,于是 `/isochrone` 整天
   503/403/502。**额度耗尽时 Tyk 网关会交替返回 403 `Quota exceeded`、429 和 nginx 502
   HTML——三个状态码,一个原因。**
2. **公网 `cycling-*` 图是坏的,而且返回 HTTP 200**(600 s 等时圈 0.031 km²,
   reachfactor 0.0016,约小 400 倍),`foot-walking` 则直接黑洞。证据见
   `pg-notes/ors-profile-outage.md`。自建是从我们自己的 pbf 重建图,所以这两个故障
   按定义不会带过来。

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
`scripts/import_poi.py` 读 POI 标签的 node/way(`shop=`/`amenity=`);ORS 读 `highway=*`
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

## 构建后必须测量的三件事(不要只看 200)

1. **`cycling-regular` 的 600 s 等时圈面积 / reachfactor。** 公网是 0.031 km² / 0.0016。
   自建若给出数 km² 且 reachfactor ~1,则 `config.py` 里 cycling 的那段 Known Issue
   和 `walking → foot-hiking` 的重映射都可以回退——**但要先量,别因为它返回 200 就信**
   (这正是上游骗过我们的方式)。
2. **`foot-walking` 是否还黑洞。** 若正常,`MODE_MAPPING["walking"]` 应改回 `foot-walking`。
3. **构建实际吃了多少内存/多长时间。** compose 里的 `XMX: 4g` 是起点不是实测值,跑完把
   真实数字写回来。

## 已知代价

- **覆盖范围从全球缩到北荷兰一省。** 这恰好与 `poi` 表(82,259 行,仅 NH)一致,所以是
  消除了一处不一致,而不是引入。要扩省就得同时重跑 `import_poi.py` 和重建图。
- **Vercel 上没有自建实例。** 生产仍走公网 50/天。真正解决要把后端搬离 Vercel——
  这和 PG 阶段 2 的 `SKIP LOCKED` worker 需要常驻进程是同一个前提。
- 图数据放在 docker volume 里,不进 git。换 major 版本要重建。
