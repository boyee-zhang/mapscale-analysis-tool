# ORS: `foot-walking` black-holes, `cycling-*` silently lies (2026-10-18)

The symptom was "every `/isochrone` and `/pois` call returns 504". The conclusion I reached
first — "ORS free tier is down / blocking this IP" — was wrong, and wrong in a way worth
recording: it is per-**profile**, and only one of the two broken profiles fails loudly.

## How it was narrowed

Each step rules out one explanation. Times are `curl -w %{time_total}` from this machine.

| probe | result | rules out |
|---|---|---|
| `dig api.openrouteservice.org` | 129.206.5.53 | DNS |
| `GET /` | 200, connect 24 ms / tls 70 ms | network, TLS, IP ban |
| `POST /v2/isochrones/foot-walking`, **no key** | **401 in 95 ms** | the auth layer being slow |
| same, **bogus well-formed key** | **403 in 94 ms** | ditto, and proves auth answers fast |
| same, **real key** | `http=000`, hangs to the timeout, `size_upload=45` | our key being invalid |
| real key, `range=60` instead of `600` | hangs identically | computation size / cost limits |
| our client at 15 s vs `curl -m 90` | both hang | our `httpx` timeout being too tight |

So: the request authenticates, the body uploads, and then nothing ever comes back. An
expired token was the leading hypothesis and it is also wrong — the key is the legacy ORS
format, a single base64 segment decoding to `{"org": "5b3ce359…", "id": "…", "h": "murmur64"}`,
with no `exp` claim to expire.

The thing that actually cracked it was testing a *different endpoint* with the same key:

| endpoint, same key | result |
|---|---|
| `POST /v2/matrix/driving-car` | **200 in 240 ms**, real durations |
| `GET /elevation/point` | 200 in 150 ms |
| `GET /geocode/search` | 200 in 277 ms |
| `POST /v2/directions/driving-car` | 200 in 114 ms |
| `POST /v2/isochrones/driving-car` | 200 in 227 ms |
| `POST /v2/matrix/foot-walking` | **hangs** |
| `POST /v2/directions/foot-walking` | **hangs** |
| `POST /v2/isochrones/foot-walking` | **hangs** |

The account, the key, the quota and the routing engine are all fine. `foot-walking` hangs on
*every* endpoint and no other profile hangs on *any* endpoint. It is one broken graph instance.

## Then: read the spec instead of guessing

I had started patching around this on the assumption that `foot-walking` might have been
retired. It has not been. The official spec is not at any of the `…/api-docs` URLs — those
are all 404. `https://docs.openrouteservice.org/openapi.json` is a tiny FastAPI index whose
one route is `/{service}/{version}/docs`, so the real document is:

```
https://docs.openrouteservice.org/openrouteservice/v2/docs    # 654 KB, OpenAPI 3.1
```

It says, authoritatively:

- `profile` enum: `driving-car, driving-hgv, cycling-regular, cycling-road, cycling-mountain,
  cycling-electric, foot-walking, foot-hiking, wheelchair, public-transport`.
  **`foot-walking` is current, not deprecated.**
- body `required: [locations, range]`; `range_type` defaults to `time`, i.e. `range` is in
  **seconds**. Our request body is valid as sent.
- `servers[0].url` is **`https://api.heigit.org`** and the paths are prefixed
  `/openrouteservice/v2/…`. We call `https://api.openrouteservice.org/v2/…`, which is now an
  undocumented legacy host (see "Open" below).
- `/v2/status` and `/v2/health` exist in the spec but 404 on the public gateway — they are
  self-host-only, so there is no way to ask the public API which graphs are loaded.

Re-running the whole matrix against `api.heigit.org/openrouteservice/v2` gives **byte-identical
behaviour**: `foot-walking` hangs, `cycling-regular` returns its degenerate shape,
`driving-car` is fine. Same backend behind both hostnames. The host migration is real but it
is not the cause.

## The second bug, which is worse

`range=600` with `attributes=["area","reachfactor"]`, `area_units=km`:

| profile | area | reachfactor | verdict |
|---|---|---|---|
| `foot-hiking` | 1.489 km² | 1.0 | healthy |
| `wheelchair` | 1.454 km² | 1.0 | healthy |
| `driving-car` | 19.279 km² | 0.0221 | healthy (low reachfactor is normal — it is relative to the profile's max speed) |
| `cycling-regular` | **0.031 km²** | **0.0016** | broken |
| `cycling-road` | 0.031 km² | 0.0006 | broken |
| `cycling-electric` | 0.031 km² | 0.0009 | broken |
| `cycling-mountain` | 0.031 km² | — | broken |
| `foot-walking` | — | — | no response at all |

A 10-minute bike ride should cover roughly 9x a 10-minute walk (~13 km²). It returns 0.031 km²
— a 200 m blob, ~400x too small — and `/matrix/cycling-regular` returns `null` durations
between two points 800 m apart, which is the same broken graph seen from another angle.

**`cycling` returns HTTP 200.** That is the whole problem. `foot-walking` hanging is ugly but
honest: it raises, the log says so, and `/pois` falls back to a geodesic circle. Cycling
produces a well-formed GeoJSON polygon of the wrong size, so nothing in our stack can tell —
not the `raise_for_status()` in `upstream/ors.py`, not the `except` in `domains/geo/service.py`,
not the circle fallback. `/pois?profile=cycling` quietly returns a handful of POI instead of a
few hundred and looks like a correct answer.

This is the same failure *class* as the invalid-polygon bug found the same day
(`stage1-poi-import.md`): a spatial upstream that answers rather than refuses. The lesson
repeats — **for geometry, a 200 is not evidence of a correct answer; only a magnitude check is.**

## Fix applied

`config.py` `MODE_MAPPING["walking"]`: `foot-walking` -> `foot-hiking`. Justified by the table
above (1.489 vs 1.454 km² against `wheelchair`, reachfactor 1.0 — the same pedestrian speed
model), not by it merely returning a 200.

`cycling` is left pointing at `cycling-regular` **on purpose**. All four cycling graphs are
equally broken, so a remap would only move the bug; and silently substituting a pedestrian or
driving graph for a bike request would be a worse lie than the one upstream is telling.

Verified after the change, through our own API, with Postgres live:

```
GET /api/main/pois?lng=4.9003&lat=52.3791&minutes=10&profile=walking
  -> 200 in 0.359 s, 37 elements, log shape="isochrone", zero timeouts
GET /api/main/pois?…&profile=driving
  -> 200 in 0.329 s, 195 elements, log shape="isochrone"
GET /api/main/pois?…&profile=driving&categories=…5 categories…&minutes=10
  -> 631 elements, 409 of them ways
```

That last line also closes the item `stage1-poi-import.md` left open: the isochrone-shaped SQL
path had only ever been verified with a hand-written GeoJSON polygon, because ORS never
answered. It is now verified against a real ORS isochrone.

## Open

- **Should `/isochrone` reject an implausible isochrone?** A cheap version is to request
  `attributes=["area","reachfactor"]` (free — it is computed anyway) and treat
  `reachfactor < 0.01` *together with* an area far below `minutes * SPEED_MAP[profile]` as an
  upstream failure, so `/pois` degrades to its circle instead of under-reporting. This turns
  the silent cycling bug into the loud `foot-walking` one. Not implemented — it changes
  `/isochrone`'s contract and needs a decision.
- **Should we move to `https://api.heigit.org/openrouteservice/v2`?** It is what the spec
  documents; our host is undocumented and behaves identically today. Low risk, but it is a
  base-URL change to the only hard external dependency, so it is a decision, not a cleanup.
- ~~`cycling` mode is broken for users until upstream fixes its graph.~~ **Answered below —
  there was no broken graph to fix.** The plausibility check in the first bullet is now the
  whole of the remaining work.
- Revisit `MODE_MAPPING["walking"]` if/when `foot-walking` recovers. Nothing detects that
  automatically, and the self-hosted measurement below shows a recovery would have to happen
  on the **public** instance to matter: prod runs on Vercel against the public API.
- ~~Self-hosting ORS~~ **done 2026-10-28** — see below. It did make `/v2/status` available,
  and that is what falsified this note's main conclusion.


---

# Re-measured against a self-hosted instance, 2026-10-28

`docker compose up -d ors`, `openrouteservice/openrouteservice:v10.1.0`, graphs built from the
same `data/noord-holland-latest.osm.pbf` that `etl/ingest/poi.py` reads. Runbook and the
build figures: `docs/ors-self-host.md`.

The prediction this note's own structure implied — *we build the graphs from our own extract, so
a broken-graph fault cannot come with us* — was half right, and the half that was wrong is the
interesting half.

## What the second instance separated

Same request as the original matrix (`range=600`, `attributes=["area","reachfactor"]`,
`area_units=km`, centre `4.9003, 52.3791`):

| profile | public, 2026-10-18 | self-hosted, 2026-10-28 | where the fault is |
|---|---|---|---|
| `foot-hiking` | 1.489 km² / 1.0 | 1.489 km² / 1.0 | — identical |
| `driving-car` | 19.279 km² / 0.0221 | 20.477 km² / 0.0235 | — differs only by OSM date |
| `cycling-regular` | 0.031 km² / 0.0016 | **0.0308 km² / 0.0016** | **not upstream — see below** |
| `foot-walking` | no response at all | **1.489 km² / 1.0, routes 731 m in 526 s** | **the public deployment** |

Areas were re-derived in PostGIS from the returned geometry rather than read from ORS's own
`area` attribute (`ST_Area(geom::geography)/1e6`): 1.4918 / 20.5117 / 0.0309 km², agreeing
within 0.2%. So the attribute is trustworthy here — but that is now a measured statement.

**`foot-walking` works when we host it.** Identical to `foot-hiking` to eight decimal places,
and it routes the same 731 m. The black hole is therefore in the public deployment, not in ORS
and not in any graph. It does *not* license re-pointing `MODE_MAPPING["walking"]`: that is one
global constant and production on Vercel talks to the public API, so re-pointing it would fix
localhost by breaking prod.

## The cycling conclusion was a single-point measurement

The blob reproduced locally, which looked at first like a much worse finding — a bug we build
ourselves. It is not. Varying **one axis, the coordinate**, against the same graph:

| centre | area | reachfactor |
|---|---|---|
| `4.9003, 52.3791` — the original test point | **0.0308 km²** | **0.0016** |
| 200 m east | 15.124 km² | 0.7703 |
| 200 m west | 14.693 km² | 0.7483 |
| 200 m north | 14.465 km² | 0.7367 |
| 200 m south | 10.144 km² | 0.5166 |
| Dam square, ~700 m west | 14.885 km² | 0.7581 |
| Haarlem | 14.867 km² | 0.7572 |
| Alkmaar | 16.082 km² | 0.8190 |
| Hoorn | 12.454 km² | 0.6343 |

10–16 km² is *the healthy number this note predicted*: "a 10-minute bike ride should cover
roughly 9x a 10-minute walk (~13 km²)". The graph is fine. `/directions` from the bad point
fails with ORS code `2009` ("Unable to find a route") while `foot-walking` covers the same
731 m, so that one coordinate snaps to a bike-disconnected component — a few metres of path
that no bike edge leaves.

The original matrix tested **every profile at one coordinate**. Four cycling profiles agreeing
read as four broken graphs; it was one bad snap seen four times. The note's own lesson, one
section up, is *"vary one axis at a time against a known-good control"* — the control here is
the location, and it was the one axis never varied.

What does **not** change: a 400x-too-small polygon still arrives as HTTP 200 with a well-formed
ring, and nothing in the stack can see it. The plausibility check in **Open** is still owed,
and is now the entire fix.

What is **not** established: whether the public graph is also healthy away from that point.
The one request budgeted to check it returned `403 {"error": "Quota exceeded"}` — the 50/day
was already spent. Untested, not confirmed.

---

# Graph coverage, measured three times, wrong twice (2026-10-29)

Question that started it: "is the building-permit layer only Amsterdam?" The data answer is
yes (10,128 of 10,306 permits). The *shape* answer — where `/bekendmakingen` and `/pois` get
a real isochrone rather than a radius fallback — needed the graph's real extent, and the
first two attempts to measure it both produced a wrong number.

## Attempt 1 — counted HTTP 200. Answer: 72/342. Wrong.

A 300 s `driving-car` isochrone from each of the 342 municipality centroids
(`ST_PointOnSurface`), counting status 200. The note two sections up says, in its own words,
*"for geometry, a 200 is not evidence of a correct answer; only a magnitude check is"* —
and I counted status codes anyway.

## Attempt 2 — added a magnitude check. Answer: 54. Still wrong.

`area >= 2.0 km²`, deliberately generous against an expected 8-10 km² for a 5-minute drive.
That removed 18 obvious blobs (0.25-1.86 km²) but left Culemborg, De Bilt, Heusden,
Hilvarenbeek, Houten, Oisterwijk, Reusel-De Mierden, West Betuwe and Zaltbommel in the
"covered" list — nine municipalities in Utrecht, Gelderland and Noord-Brabant, nowhere near
a Noord-Holland extract.

## Attempt 3 — grouped by area value. Answer: 53, and the tell was duplication.

| probe | area | reachfactor |
|---|---|---|
| Culemborg | **3.47 km²** | **0.0159** |
| De Bilt | **3.47 km²** | **0.0159** |
| Heusden | **3.47 km²** | **0.0159** |
| Hilvarenbeek | **3.47 km²** | **0.0159** |
| Houten | **3.47 km²** | **0.0159** |
| Oisterwijk | **3.47 km²** | **0.0159** |
| Reusel-De Mierden | **3.47 km²** | **0.0159** |
| West Betuwe | **3.47 km²** | **0.0159** |
| Zaltbommel | **3.47 km²** | **0.0159** |
| Haarlem | 8.83 km² | 0.0405 |
| Hoorn | 11.51 km² | 0.0528 |
| Den Helder | 13.27 km² | 0.0608 |

Nine different coordinates, spread over three provinces, return **byte-identical** areas.
That is not nine isochrones; it is **one stranded motorway fragment reached from nine
different snap points**. Geofabrik extracts keep ways that cross the cut line, so a point far
outside the province still snaps onto a few kilometres of orphaned A-road and ORS builds a
perfectly well-formed isochrone on it.

Grouping all 342 answers by area:

```
342 municipalities; 72 answered 200, 270 refused honestly (ORS code 3099)
  distinct area -> 53   genuinely inside the graph
  shared  area -> 19   the same fragment seen from several places
     9 x 3.47 km2 | 6 x 0.69 km2 | 2 x 1.80 km2 | 2 x 0.93 km2
```

**53** is the real figure, and the list is Noord-Holland plus the extract's immediate edge
(Hillegom / Lisse / Teylingen in Zuid-Holland, Eemnes / Baarn / Bunnik / Stichtse Vecht in
Utrecht). The 270 refusals are the *honest* failure: `/bekendmakingen` and `/pois` fall back
to a geodesic circle there, which they report as `shape: "circle"`.

## The lesson, which is new and sharper than the one above it

A magnitude check catches a blob that is *absurd*. It does **not** catch a blob that is
*plausible* — 3.47 km² is a perfectly believable 5-minute drive in a small town. What caught
it was that **distinct inputs produced identical outputs**.

> **When a probe sweeps many inputs, check the answers for duplicates before trusting any of
> them. Identical outputs from different inputs mean the thing you varied is not what the
> system answered from.**

A caveat on the method itself, so the 53 is not over-read: Amsterdam's own centroid returns
only 2.77 km², because `ST_PointOnSurface` of its multipolygon lands in the port. Centroid
probing measures *the centroid*, not the municipality. The 53 is a floor.

## What this does not change

The plausibility check in **Open** is still owed, and this is now its third independent
motivation (cycling blob, invalid rings, stranded fragments). Note that the check as
specified — `reachfactor < 0.01` plus an area far below `minutes * SPEED_MAP[profile]` —
would **not** have caught the 3.47 km² case: reachfactor 0.0159 is above the threshold and
3.47 km² is not far below expectation. Duplicate-detection is a different instrument, and it
only works across a sweep, not on a single request.

---

# `reachfactor` 是什么,以及我上一条结论的订正 (2026-10-29)

上一轮我写了"**plausibility check 必须按面积判,不能用 reachfactor**",理由是乌特的
reachfactor 是 0.0188–0.1476,高于规格里的 `< 0.01`。**理由站不住 —— 两者是同一个信号。**

官方文档没有给公式(论坛上维护者说它是"等时圈占一个按该 profile 平均速度画的圆的比例",
但那条帖子里的算例是错的)。所以反解:若 `rf = area / (π·(v·t)²)`,则
`v = sqrt(area/rf/π)/t`。如果 v 在不同地点、不同 range 下是常数,公式就被确认了。

实测 4 个地点 × 2–3 个 range:

| profile | 反解出的 v | 波动 |
|---|---|---|
| `driving-car` | **100.00 km/h** | 99.93 – 100.03 |
| `cycling-regular` | **15.00 km/h** | 14.86 – 15.00 |
| `foot-hiking` | **4.00 km/h** | 未被截断的读数全是 4.00 |

**公式确认,并且 rf 在 1.0 处被截断** —— 每一个反解出 >4.00 的步行读数,rf 都正好是 1.0000
(Haarlem 300 s / 600 s 没被截断,反解出的正是 4.00 和 4.00)。

所以:

```
reachfactor ≡ area / (π · (v_mean · t)²)      v_mean: 步行 4 / 骑行 15 / 驾车 100 km/h
```

**rf 和 area 携带完全相同的信息**,rf 只是把 area 按 `(v·t)²` 归一化了。订正如下:

- ❌ 我上一轮说的"必须按面积、不能用 reachfactor"**是错的**。
- ✅ 真正的问题是**阈值定错了**:乌特步行是期望值的 **1.9%**(rf 0.0188),而规格写的是
  `rf < 0.01`,也就是只抓低于期望 1% 的情况 —— 太宽松了。
- ✅ 而且 rf 的归一化**正是我们想要的**:它自带 profile 和 range 的量纲,一个阈值可以同时
  管步行/骑行/驾车,按 area 判则要为每个 profile 和每个 minutes 另算期望值。

还有一个必须说的局限:**rf 抓不到"碎片"类故障的根因,只能抓到它的后果**。阿姆斯特丹那个
骑行坏点 600 s 反解出 14.86 km/h —— 面积和 rf 一起缩小,比值不变,所以**自洽**。
rf 能告诉你"这个圈比应该的小 99.84%",但它永远不会告诉你"你量到的是一段孤立路段"。
那个只有**跨多次请求比对重复值**才看得见。

两件工具,两种故障:
- **单次请求** → reachfactor 阈值(需要把 0.01 调高,0.01 太宽松;乌特要 >0.15 才能覆盖)。
- **一次扫描** → 查重复值(不同输入返回相同输出)。
