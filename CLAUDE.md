# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MapScale is a geographic analysis tool for the Netherlands. It started as an urban accessibility
tool (travel-time isochrones + POI density) and has grown a second subsystem: a three-tier
address search. A housing-market subsystem (rental listings, choropleth, AI reports) existed and
was removed — see the note at the end of this file.

**Tech Stack:**
- Frontend: React 18 + Vite 6, MapLibre GL 5, Axios, Recharts
- Backend: FastAPI (Python 3.12), HTTPX (async), deployed as a single Vercel serverless function via Mangum
- Database: PostgreSQL 16 + PostGIS 3.4, local Docker, `asyncpg` with hand-written SQL (no ORM)
- Cache: Upstash Redis (REST API)
- Search: Elasticsearch 8.14 (optional — local via Docker, falls back to PDOK/Nominatim)
- External APIs: OpenRouteService, Overpass, PDOK Locatieserver + WFS, Nominatim, TomTom Traffic

The AI analysis panel calls the sibling `mapscale-ai-service` repo through the `/api/ai/*` proxy;
this repo holds no LLM client of its own.

## Development Commands

### Everything at once
```bash
./scripts/dev.sh      # postgres (--wait) -> migrations -> uvicorn :8000 + vite :5173
```
Ctrl-C stops all of it. The two optional services are left out on purpose: `ors` because the
first start builds routing graphs, and `elasticsearch` because `/search` already degrades to
PDOK → Nominatim and the local `esdata` volume is empty. The individual commands below are
still the reference; `dev.sh` only chains them.

### Frontend
```bash
cd frontend
npm install
npm run dev       # http://localhost:5173
npm run build
```

### Backend
```bash
uvicorn api.main.app:app --reload   # http://localhost:8000
```

Run from the repo root — `api/main/` is a package and imports are relative to it.

### Postgres / PostGIS
```bash
docker compose up -d postgres               # :5432, db/user/pass all "mapscale"
python scripts/migrate.py                   # apply pending migrations (idempotent)
python scripts/migrate.py --status          # list applied / pending
python scripts/seed_municipalities.py       # seed municipality from PDOK CBS WFS (~342 rows)
python scripts/import_poi.py data/noord-holland-latest.osm.pbf   # OSM POI import
python scripts/import_bekendmaking.py --gemeente Utrecht --from 2026-01-01  # announcements
```

`import_bekendmaking.py` is scoped **per municipality** (or an RD New circle), because those
are the only two ways KOOP's SRU service narrows by place. Widening coverage is therefore
re-running it, once per municipality — there is no national mode. It only ever upserts, so a
re-run is idempotent and a short fetch costs coverage, never correctness. Both importers read
`.env` via `load_dotenv`; neither requires `DATABASE_URL` to be exported by hand.

`import_poi.py` needs `osmium` (pyosmium) from `requirements-local.txt` — a Python
package, not `brew install osmium-tool`; nothing system-level is required. It takes a
Geofabrik `.osm.pbf` extract, which is downloaded by hand into the gitignored `data/`
(`https://download.geofabrik.de/europe/netherlands/`). The tag whitelist is read from
`poi_category_rule`, so widening the import is an INSERT, not a code change. The run is
named after the extract and the soft-delete pass is confined to the bounding box of what
that run staged, so importing one province cannot delete another.

Migrations are numbered `.sql` files in `migrations/`, applied forward-only by a
hand-written runner; there is no `down`. A file named `NNNN_x.noxact.sql` runs **outside**
a transaction, which is required for `CREATE INDEX CONCURRENTLY`.

The container's memory parameters are deliberately small (`shared_buffers=128MB`,
`work_mem=4MB`) so that disk spills and autovacuum activity become observable at ~10M rows
instead of ~1B. Do not "fix" them. The image is `imresamu/postgis` rather than
`postgis/postgis` because the latter publishes amd64 only, and emulation on Apple Silicon
makes every `EXPLAIN ANALYZE` timing meaningless.

### Self-hosted OpenRouteService (optional)
```bash
docker compose up -d ors          # :8080 — FIRST START BUILDS 3 ROUTING GRAPHS, tens of minutes
curl localhost:8080/ors/v2/health
```
The service carries `profiles: ["ors"]`, which is what actually keeps it out of a bare
`docker compose up -d` — an earlier comment claimed that without the profile, and
`docker compose config --services` listed it. Naming the service activates its profile, so
the command above is unaffected. Reads the same
`data/*.osm.pbf` as `import_poi.py` (read-only), so coverage is Noord-Holland only. Point the
backend at it with `ORS_BASE_URL=http://localhost:8080/ors/v2`. The why, the measured
preconditions and the three things to measure after the build are in `docs/ors-self-host.md`.

### Monitoring (optional) — Prometheus + Grafana
```bash
docker compose up -d prometheus grafana postgres-exporter   # :9090, :3000
.venv/bin/python scripts/loadtest.py --scenario permits     # then watch the dashboard
```
All three carry `profiles: ["monitoring"]`, which is what keeps them out of a bare
`docker compose up -d`. The backend exposes `/metrics` — **not** under `/api/main`, since
that prefix is what vercel.json routes to the serverless function. `prometheus_client`
lives in `requirements-local.txt`, so `/metrics` returns **503, not an empty 200**, where
it is absent; an empty 200 charts as a flat zero that looks like "no traffic". Scraping
Vercel would be pointless regardless: each invocation is a fresh process.

Request labels use the **route template** (`scope["route"].path`), never the raw path —
`/api/main/pois?lng=…&lat=…` as a label is one time series per coordinate the browser
ever sent. Pool and loop-lag gauges are sampled by a 1 s background task rather than read
inside the scrape handler, because reading them on scrape observes them only at the one
moment the pool is guaranteed idle.

Measured ceilings and which resource binds them: `docs/monitoring.md`. Headline — the wall
was **constructing an `httpx.AsyncClient` per call**: building its TLS context costs
**13.88 ms of CPU**, and with the Upstash host NXDOMAIN every request paid that for a
cache lookup that could never hit. `api/main/http_client.py` is now the one shared client;
**use it, never `httpx.AsyncClient(...)` in a request path**. Measured: 22.3 → 7.5 ms CPU
per request, 40.9 → 119.4 req/s, p95 1495 → 364 ms, with the dead cache still configured
and still called every request. Not the pool (10→30 changed nothing), not ORS (4% of
request time), not Postgres (1.3–4.3 of 8 cores, one active query most samples).
`--workers 4` buys +80% but only adds cores to burn on the same waste — fix the waste
first. The doc records the first, wrong conclusion too.

### Elasticsearch (optional)
```bash
docker compose up -d elasticsearch   # :9200, single-node, security disabled
python scripts/ingest_addresses.py   # seed nl_addresses from PDOK
```

### Lint & Test
```bash
pip install -r requirements-local.txt
ruff check api/                          # line-length 120
BASE_URL=http://localhost:8000 pytest    # integration tests hit a live server
pytest tests/unit                        # unit tests, no server needed
```

CI lives in two workflows: `ci.yml` runs `ruff check api/` + `npm run build`, and `test.yml`
starts a uvicorn and runs unit + integration tests with the `ORS_API_KEY`/`TOMTOM_API_KEY`
secrets. **Both trigger only on push/PR to `main`**, so pushing a feature branch runs nothing.
Isochrone/directions tests are marked `requires_ors` and skip in CI — not because of an IP ban
(that hypothesis was disproved; `docs/pg-notes/ors-profile-outage.md`) but because the free tier
is 50 requests/day/endpoint and the `cycling-*` graphs answer 200 while being broken.

### Environment variables (`.env` at repo root)
```
ORS_API_KEY=                   # required for isochrone + directions
ORS_BASE_URL=                  # optional; defaults to the public host. See docs/ors-self-host.md
TOMTOM_API_KEY=                # required for traffic
DATABASE_URL=                  # optional; omit to run without Postgres (see below)
DB_POOL_MAX_SIZE=              # optional, default 10. An env var so it can be VARIED while
                               # measuring — that is the only way to tell "the pool is the
                               # wall" from "the pool is merely full". Measured: it is not.
ES_URL=                        # optional; omit to disable ES and fall back to PDOK/Nominatim
UPSTASH_REDIS_REST_URL=        # optional; omit to disable caching (degrades silently)
UPSTASH_REDIS_REST_TOKEN=
```

## Architecture

### Request flow
```
Browser → api.js → Vite proxy → FastAPI (api/main/app.py)
                                    ↓ middleware: RequestLogging → CORS
                                    ↓ router (api/main/routers/)
                                    ↓ cache check (Upstash)
                                    ↓ api/main/upstream/ or a domain service
                                    ↓ upstream API
```

### Proxy / routing
`frontend/vite.config.js` (dev):
- `/api/main/*` → `http://localhost:8000` (path forwarded as-is; FastAPI routes include the prefix)
- `/api/ai/*` → `https://mapscale-ai-service.vercel.app`, rewritten to `/api/*`

`vercel.json` (prod):
- `/api/main/:path*` → `/api/main/index` (the single Mangum-wrapped function)
- `/api/ai/:path*` → `https://mapscale-ai-service.vercel.app/api/:path*`
- everything else → `/index.html` (SPA fallback)

### Backend layout
```
api/main/
  app.py          FastAPI instance, middleware, exception handlers, router registration
  index.py        re-exports app for Vercel
  config.py       env vars + MODE_MAPPING + SPEED_MAP
  errors.py       ExternalServiceError, InternalError
  cache.py        Upstash KV get/set
  db.py           asyncpg pool (created in the app lifespan), acquire()/transaction()
  logger.py       JSON logging + request-ID middleware
  upstream/       transport only: ors, overpass, pdok, tomtom, es_client
  domains/
    geo/          spatial primitives: isochrone + POI-inside-a-shape (service.py)
    bekendmaking/ official announcements for a place and a time window (service.py);
                  composes domains/geo's isochrone, owns no shape of its own
  routers/        isochrone, pois, bekendmakingen, directions, region, traffic,
                  search, health — isochrone/pois/bekendmakingen are thin callers of
                  domains/; the rest are not yet folded in (refactor phases B & C)
```

**`upstream/` is a transport layer only.** It may not contain business rules, caching
decisions, or domain types. Judgement call: if a function in `upstream/` needs to know what an
"isochrone" is, it belongs in a domain instead. Organising it per upstream API is correct
*because* the business semantics now live in `domains/`.

**Every Postgres-backed path must degrade, not fail, when `DATABASE_URL` is unset.**
`db.enabled()` is the guard; the pre-Postgres code path stays in place behind it, exactly
as `cache.py` degrades when Upstash is unconfigured. This keeps the Vercel deployment
working while Postgres is local-only. Current tables: `municipality` (stage 0); `etl_run`,
`poi_category`, `poi_category_rule`, `poi`, `poi_import` (stage 1); `bekendmaking`,
`bekendmaking_locatie` (official announcements, loaded by `scripts/import_bekendmaking.py`). `geocode_cache` is also
still there but no longer read or written — its only caller was the housing geocoder.

A refactor is in progress — see `docs/architecture-refactor-plan.md` (structure) and
`docs/postgres-refactor-plan.md` (persistence). Phase A (this layout) is done, as is
Postgres stage 0; `api/main/` stays the package root so no URL or deploy config changes.
Raw `EXPLAIN` / lock / vacuum output from each stage is archived in `docs/pg-notes/`.

### Endpoints

All under the `/api/main` prefix.

| Endpoint | Method | Upstream | Cache TTL |
|---|---|---|---|
| `/health` | GET | — | — |
| `/isochrone` | GET | ORS | 24h |
| `/pois` | GET | `poi` table (Overpass as fallback) | 6h on the Overpass path only |
| `/bekendmakingen` | GET | `bekendmaking` tables + ORS for the shape | — |
| `/region` | GET | PDOK reverse | 7d |
| `/directions` | GET | ORS | — |
| `/bulk_directions` | GET | ORS (`asyncio.gather`) | — |
| `/traffic/tile-url` | GET | — (static URL) | — |
| `/traffic/flow` | GET | TomTom Flow | 5m |
| `/traffic/incidents` | GET | TomTom Incidents | 5m |
| `/search` | GET | ES → PDOK → Nominatim | — |

Cache keys are `{feature}:{param}:{param}:…`, e.g. `iso:{lng}:{lat}:{minutes}:{profile}`.
The `(lng, lat, minutes, profile)` shape goes through `cache.geo_cache_key()` — `/pois` reads
the isochrone `/isochrone` wrote, so the two keys must agree to the last decimal, and they
did not (`iso:` at `.5f`, `pois:` at `.3f`). Do not re-inline the format.

`domains/geo/service.py` also keeps an in-process **single-flight** map in front of the ORS
isochrone call, keyed on that same cache key. The cache alone cannot deduplicate `/isochrone`
and `/pois`: the browser issues them concurrently for identical parameters, so both miss
before either writes — measured 38 ORS calls for 18 unique parameter sets. The free tier is
**50 requests/day/endpoint**, so the amplifier, not the usage, is what exhausts the quota.
Both awaits are `asyncio.shield`ed; without that, one client disconnecting cancels the shared
task and fails the other request too. This is per-process, so it does not help across Vercel
instances — see `docs/ors-self-host.md` for the structural fix.

`/pois` answers from the `poi` table when `db.enabled()`, clipping to the real isochrone
polygon with `ST_Contains`; it degrades to a geodesic-buffer circle (still Postgres) if ORS is
unavailable, and to the old node-only Overpass circle if Postgres is not configured. It takes a
repeatable `categories` param (keys from `poi_category`), defaulting to the legacy
supermarket/convenience/gym set. Multi-category queries are written as `unnest` + `CROSS JOIN
LATERAL` with an inner `LIMIT`. **That rewrite's advantage is scale-bound and has already
inverted**: at 82,259 rows btree_gist could not use a `ScalarArrayOpExpr` as an index condition
and `unnest` was 21x better (1708 buffers against 80), but at 512,844 rows the planner picks a
*Bitmap* Index Scan, which loops the array in the executor, so `category = ANY($1)` is back
inside the `Index Cond` and is now slightly ahead (91 buffers against 98). The inner `LIMIT` is
still load-bearing. Re-measure before quoting either number. Likewise the
radius is a buffer polygon, not `ST_DWithin(geom::geography, …)`, which is unindexable against a
`geometry` GiST. Both are measured in `docs/pg-notes/stage1-poi-import.md`; do not "simplify".

`/bekendmakingen` answers "what has been happening here, month by month" over the same
isochrone. `building_permits=true` is shorthand for `doc_type=omgevingsvergunning` +
`activiteit=bouwen` — `doc_type` alone also covers felling, signage and zoning. Four things
about its contract are easy to get wrong:
- `total` and `months` count every match; `items` is capped at 500 and `truncated` says so.
  **Never re-derive the month histogram from `items`** — a truncated list under-reports.
- `by_activiteit` counts do **not** sum to `total`: one permit can carry several activities.
  Correct as a bar chart, wrong as a pie.
- `stage` (aanvraag / ontwerp / besluit / overig) is **derived from the title**, not a field
  the source publishes. 99.8% of building permits classify; the UI labels it as derived.
- without `DATABASE_URL` it returns `shape: "unavailable"`, not `total: 0`. There is no
  Overpass-equivalent to degrade to, and "no source" must not look like "no permits".

The spatial predicate is `ST_Intersects`, not the `ST_Contains` `/pois` uses: announcement
geometry is 16% POLYGON and 13% LINESTRING, and a plot straddling the isochrone edge is
within reach. One row per publication comes from `DISTINCT ON` in the same index pass —
a publication carries 2.5 geometries on average. Measured in
`docs/pg-notes/bekendmaking-map-layer.md`; do not "simplify" either.

**`db.py` sets `plan_cache_mode=force_custom_plan` on every pooled connection, and that is
load-bearing.** asyncpg sends queries as server-side prepared statements and caches them;
PostgreSQL switches to a generic plan on the 6th execution, and our spatial queries pass the
search shape as a parameter, so the planner has no bounding box to estimate from. Measured:
the same wide query took 2.4-3.1 s for five executions and **104 s on the sixth**, reproduced
independently in psql with PREPARE/EXECUTE (77.5 s on the sixth). `/pois` passes its polygon
the same way and had the same latent exposure. Do not remove the setting.

Mode constants live **only** in `config.py`:
- `MODE_MAPPING`: `walking → foot-hiking`, `cycling → cycling-regular`, `driving → driving-car`
- `SPEED_MAP` (m/min): `walking 80`, `cycling 250`, `driving 800` — used to derive the Overpass search radius

`upstream/overpass.py` fails over across three mirrors (kumi.systems → overpass-api.de → openstreetmap.fr).

### Search waterfall (`routers/search.py`)
1. **Elasticsearch** `nl_addresses` — `multi_match` with per-field weights (overridable via
   `w_street`/`w_city`/`w_postcode`/`w_label` query params) + postcode `term` + `phrase_prefix`.
   Accepted only above `ES_MIN_SCORE` (15.0).
2. **PDOK free API** — results filtered by `_pdok_relevant()`: every long token in the query must match.
3. **Nominatim** — last resort, handles company names and POIs.

Results from tiers 2–3 are written back into ES via `async_bulk` (fire-and-forget), so the index
self-enriches over time.

A second search implementation (`/search_lab` against an `osm_places_lab` index, with edge n-gram
autocomplete and Gaussian distance decay) lives only on the unmerged branch
`feature/search-index-lab`. It is **not on main** and is not to be merged as-is — the valuable parts
are to be folded into production `/search` during the refactor.

### Frontend
`MapContainer.jsx` is the single source of truth:
- `params`: `{ center: { lng, lat }, minutes, mode }` — any change triggers a full re-fetch
- `data`: `{ iso, pois, loading }`
- Re-fetch driven by `useEffect([isReady, params.center, params.minutes, params.mode])`

`useMap.js` initialises MapLibre and returns `{ map, isReady }`. All map mutations are imperative,
inside `useEffect` in child components that receive `map` as a prop. **Never mutate the map before
`isReady === true`.**

POIs are rendered as returned — `/pois` now clips to the isochrone in Postgres, so the
`turf.booleanPointInPolygon` `useMemo` that used to do it in the browser is gone, and `@turf/turf`
has been dropped from `package.json`. The React key is `${poi.type}/${poi.id}`, not `poi.id`:
the endpoint returns ways as well as nodes now, and the two id spaces overlap.

`PoiMarker` takes only `{ poi, map }`. It used to take `centerLoc`/`mode`/`setHoveredRoute` and
fetch `/directions` on hover, but the state value behind that setter was destructured away in
`MapContainer`, so each hover spent one of the 50 daily ORS calls on a route nothing drew. The
fetch was removed rather than wired up — drawing the route is a feature, not a bug fix, and
`api.fetchDirections` is still there if it is ever wanted.

Components: `SearchBar`, `CenterMarker`, `PoiMarker`, `PoiPopup`, `LegendPanel`,
`AnalysisPanel` + `AnalysisResultPanel` (AI service), `PermitLayer` + `PermitPanel`.

`PermitLayer` is a GeoJSON source plus two circle layers, **not** one DOM marker per item
the way `PoiMarker` is: `/bekendmakingen` returns up to 500 and a driving isochrone hits
that cap every time. Both circle layers are interactive and both are in `MapContainer`'s
click guard — the dot is 2-7px and the halo around it is 4-18px, so hit-testing only the dot
let a click that visibly landed on a permit fall through and move the centre instead.
`e.originalEvent.stopPropagation()` does **not** stop MapLibre's own dispatch to the bare
`map.on('click')` handler; the guard has to live in that handler, via
`queryRenderedFeatures`.

`AnalysisResultPanel` still renders a **Housing Price Trend** chart. That is deliberate and is
not part of the removed subsystem: the data is CBS OData from the sibling `mapscale-ai-service`
repo (`raw.housing.trend`), not Holland2Stay.

---

## Coding Standards

### 1. Async error handling
Every async operation must be wrapped with meaningful error propagation. Silent failures are not acceptable.

**Backend:** upstream calls live in `upstream/` and must translate HTTPX failures into
`ExternalServiceError` (the app-level handler maps it to 502, or 504 for timeouts).
Routers catch `ExternalServiceError`/`HTTPException` and re-raise, and wrap anything else in
`InternalError` after logging with `exc_info=True`.

```python
# upstream/ — translate upstream failures
try:
    resp = await client.get(url, params=params)
    resp.raise_for_status()
except httpx.TimeoutException as e:
    raise ExternalServiceError("ors", 504, str(e)) from e
except httpx.HTTPStatusError as e:
    raise ExternalServiceError("ors", e.response.status_code, e.response.text) from e
except httpx.RequestError as e:
    raise ExternalServiceError("ors", 502, str(e)) from e

# routers/ — pass through known errors, wrap unknown ones
try:
    ...
except (ExternalServiceError, HTTPException):
    raise
except Exception as e:
    logger.error("…failed", exc_info=True, extra={"error": str(e)})
    raise InternalError(str(e)) from e
```

Use `asyncio.gather(*tasks, return_exceptions=True)` for bulk calls so one failure doesn't abort the batch.

**Frontend:** `api.js` methods either handle errors internally or throw so callers can distinguish.
Never return a raw promise chain without a `.catch()`.

### 2. Modular API definitions with type annotations
`frontend/src/api.js` is the single contract between the frontend and both backends. Every method
carries a JSDoc block documenting params and return shape.

FastAPI endpoints declare a `response_model` or an explicit return annotation, and use `Query(...)`
with `ge`/`le`/`min_length` constraints instead of hand-rolled validation. Shared constants
(`MODE_MAPPING`, `SPEED_MAP`) are defined once in `config.py` — never duplicate them in a router.

### 3. UI logic/style decoupling
Components must not mix inline style objects with conditional logic inside JSX. Styles belong
outside the component function — module-level `const` objects or a co-located CSS file.
Imperative DOM styling (e.g. in `PoiMarker`) uses predefined style objects, not ad-hoc
`Object.assign` literals.

```jsx
// wrong — logic and style tangled in JSX
style={{ opacity: isLoading ? 0.6 : 1, transform: isActive ? 'scale(0.98)' : 'scale(1)' }}

// correct — logic picks a named variant, style stays outside
const buttonStyle = { ...baseButtonStyle, ...(isLoading && disabledOverride) };
```

### 4. Structured logging on key paths
**Backend:** use `get_logger("router.x")` / `get_logger("client.x")`, never `print()`. Log at
handler entry with key params and again before returning with the result shape. Context goes in
`extra={}`, not interpolated into the message — the formatter emits JSON and the middleware injects
`request_id` (also returned as the `X-Request-ID` header).

The `upstream/` modules keep the `client.*` logger prefix even though the directory was renamed —
changing it would alter log output, which the structural move deliberately avoided.

```python
logger.info("isochrone", extra={"lng": lng, "lat": lat, "minutes": minutes, "profile": profile})
logger.info("← isochrone", extra={"features": len(data.get("features", []))})
```

**Frontend:** `console.log` param changes that trigger fetches, API response summaries, and map
lifecycle events.

```js
console.log('[MapContainer] params changed, fetching', params);
console.log('[api] fetchIsochrone response', { featureCount: data.features?.length });
```

---

## Known Issues

- **A `cycling-*` isochrone can be ~400x too small and still answer HTTP 200, so `cycling`
  mode silently under-reports.** At `(4.9003, 52.3791)` a 600 s cycling isochrone is a
  0.031 km² blob (`reachfactor` 0.0016 against foot's 1.0) and `/directions` from it fails
  with ORS code 2009. Because it is a 200 with a well-formed polygon, neither
  `raise_for_status()` nor the `/pois` circle fallback can detect it.
  **Corrected 2026-10-28:** this was recorded as "all four cycling graphs are broken upstream",
  which did not survive a second coordinate. A self-hosted v10.1.0 built from our own extract
  reproduces the blob at that exact point and returns 10–16 km² with `reachfactor` 0.50–0.82
  at Haarlem, Alkmaar, Hoorn and 200 m in any direction from the test point — the healthy
  figure the original note itself predicted. The point snaps to a bike-disconnected component.
  The original conclusion came from testing every profile at one coordinate, so it measured
  the coordinate. Whether the *public* graph behaves the same away from that point is
  **untested** (its 50/day quota was spent that day).
  `MODE_MAPPING["cycling"]` is still **not** re-pointed, and the plausibility check is still
  owed — it is what would have caught this in either reading.
  `foot-walking` is a different fault: the public instance black-holes it entirely, while a
  self-hosted instance serves it identically to `foot-hiking` (1.489 km², `reachfactor` 1.0,
  routes 731 m), so that one is in the deployment, not in ORS or a graph. `walking` stays
  mapped to `foot-hiking` regardless, because production on Vercel talks to the public API —
  re-pointing it would fix localhost by breaking prod. Both profiles are still current in the
  official spec, so neither is a deprecation. Full evidence and the narrowing steps:
  `docs/pg-notes/ors-profile-outage.md`.
- The official ORS OpenAPI spec is at `https://docs.openrouteservice.org/openrouteservice/v2/docs`
  (every `…/api-docs` URL 404s, and the `/dev/` docs page is a JS SPA that fetches it). It
  declares `servers[0]` as **`https://api.heigit.org`** with paths prefixed
  `/openrouteservice/v2/…`; the `ORS_BASE_URL` default is still the undocumented legacy
  `https://api.openrouteservice.org/v2`. Measured identical behaviour, so migrating is now a
  one-env-var decision rather than a bug.
- **The ORS free tier is 50 requests/day/endpoint** (measured; the pricing page is JS-rendered
  and the limit is not documented). When it is exhausted the Tyk gateway returns 403
  `{"error":"Quota exceeded"}`, 429 and an nginx **502** HTML page interchangeably — three
  status codes, one cause; do not debug them as three problems. Quota is counted per endpoint,
  and **403 responses carry no `x-ratelimit-*` headers**, so reading the remaining quota
  requires probing an endpoint that still works. `docs/ors-self-host.md` is the way out.
- `/traffic/flow` has an `api.js` method but no caller; `/bulk_directions` has no frontend method.
- Traffic data in the AI service is a PV27 (North Holland) provincial proxy applied to **every**
  municipality; results for other provinces are not location-specific.
- `ruff check tests/` reports 3 pre-existing violations (two unused `pytest` imports in
  `tests/integration/`, one `E741` in `test_search_logic.py`). CI only lints `api/`, so they
  are invisible there.
- No test coverage for any error path (502/504 handling). Removing the housing subsystem also
  removed `tests/unit/test_geocode_key.py`, which was 16 of the 25 unit tests — `pytest
  tests/unit` is now 9, all of them `test_search_logic.py`.
- No rate limiting; upstream quotas are the only backpressure.

---

## Removed: the housing subsystem

Removed 2026-10-28 because the feature is no longer supported. It was `/housing/*` (8 endpoints),
`api/main/domains/housing/` (11 modules), the `HousingLayer` / `HousingChatPanel` /
`CityReportPanel` components, and the Holland2Stay provider.

What that took with it, so the absences are not mistaken for gaps:

- **`openai` and `curl_cffi`** left `requirements.txt`; they had no other caller. This repo now
  has no LLM client — the AI analysis panel calls `mapscale-ai-service` over `/api/ai/*`.
- **`ETL_CRON_TOKEN` and `DEEPSEEK_API_KEY`** left `config.py` and the `.env` block.
- **`geocode_cache`** is an orphan table by decision: `0001_init.sql` is left untouched so the
  Postgres stage 0 narrative stays intact, and no migration drops it. Nothing reads or writes it.
- **`municipality` was kept** — it is not housing-specific. `routers/search.py`,
  `upstream/pdok.py`, `upstream/es_client.py`, `scripts/seed_municipalities.py` and
  `scripts/ingest_addresses.py` all use it.
- **`cache.py` was kept** (`/region`, `/traffic`, `domains/geo`); only the housing-specific
  `domains/housing/upstash.py` went. Note the configured Upstash instance no longer resolves
  (NXDOMAIN as of 2026-10-28), so caching is silently degraded — `cache.py` handles that by
  design, which is why nothing fails.
- `docs/data-source-feasibility.md` and the housing sections of the two refactor plans were
  deleted. `docs/pg-notes/` never mentioned housing and is untouched.
