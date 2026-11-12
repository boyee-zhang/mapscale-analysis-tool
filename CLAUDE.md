# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MapScale is a geographic analysis tool for the Netherlands. It started as an urban accessibility
tool (travel-time isochrones + POI density) and has grown a second subsystem: a three-tier
address search. A housing-market subsystem (rental listings, choropleth, AI reports) existed and
was removed — see the note at the end of this file.

**Tech Stack:**
- Frontend: React 18 + Vite 6, MapLibre GL 5, Axios, Recharts
- Backend: FastAPI (Python 3.12), HTTPX (async), run as a container behind nginx (`deploy/`)
  - **Vercel was dropped 2026-11-12.** It was never actually wrapped in Mangum either — `mangum` sat in `requirements.txt` with no importer anywhere in `api/`, so Vercel's Python runtime had been serving the ASGI app directly all along. Both are gone.
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
re-running it, once per municipality. **Corrected 2026-11-04: "there is no national mode" was
wrong, and it was load-bearing.** SRU needs no place scope at all — `dt.type == X and
dt.date >= Y` returns the whole country (2026: evenementenvergunning 34,481, omgevingsvergunning
303,186, omgevingsmelding 23,036, verkeersbesluit 21,011). Widening is a scope argument, not 342
imports. The per-municipality flag is still how this script is *invoked*, not a limit of the
upstream. It only ever upserts, so a
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
./scripts/monitoring.sh up       # :9090 Prometheus, :3000 Grafana, then prints target health
./scripts/monitoring.sh down     # stops ONLY these three. A bare `docker compose down`
                                 # removes mapscale-pg and leaves all four profiled
                                 # containers running — see below.
./scripts/monitoring.sh status

# Stopping EVERYTHING. The profiles are not optional here: a bare `docker compose down`
# targets only the services WITHOUT a profile, so it removes mapscale-pg and then fails
# with "Network ... Resource is still in use" because ors/prometheus/grafana/
# postgres-exporter are still attached (measured 2026-10-30; an earlier note in this file
# claimed it took ors with it, which was wrong in the direction that matters — it cleans
# up LESS than it looks, and the one thing it does clean up is the database).
# `docker compose ps -a` lists all five, which is exactly what made that easy to get
# wrong: ps sees more than down targets.
docker compose --profile ors --profile monitoring down   # 5 containers + network
# No -v, so all seven volumes survive (verified: identical `docker volume ls` before and
# after). `-v` is the command that actually costs 40 minutes of graph rebuilding.
.venv/bin/python scripts/loadtest.py --scenario all         # every LOCAL endpoint
.venv/bin/python scripts/loadtest.py --scenario search --probe 5   # third-party: timed, not loaded
```
`loadtest.py` **refuses to saturate** `region`, `search`, `traffic_flow` and
`traffic_incidents`: each leaves the machine on every request, so hammering them measures
PDOK/Nominatim/TomTom's rate limiter and burns a metered quota or abuses a free public
service. The constraint lives in `THIRD_PARTY` in the script, not in a comment. `--probe N`
times them sequentially instead.
All three carry `profiles: ["monitoring"]`, which is what keeps them out of a bare
`docker compose up -d`. The backend exposes `/metrics` — **not** under `/api/main`, since
that prefix is what `deploy/nginx.conf` proxies to this service. `/metrics` returns
**503, not an empty 200**, where `prometheus_client` is absent; an empty 200 charts as a flat
zero that looks like "no traffic". nginx has **no `/metrics` location**, so it is unreachable
from outside the compose network — `deploy/prometheus.yml` scrapes `api:8000` directly, on the
network, which is also why the package moved into `requirements.txt`.

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

Full-surface measurement (2026-10-29, warm, generator ceiling 5,290 req/s): `/health`
**4,346 req/s**, `/traffic/tile-url` 3,062, `/pois` 844 deduplicated and 445 when every
request is distinct, `/directions` 431, `/isochrone` 410, `/bekendmakingen` 142,
`/bulk_directions` 65. Carry forward the **per-core** costs, not the peaks: 0.67 / 3.55 /
7.22 ms of CPU per request for `/health` / `/pois` / `/bekendmakingen`, i.e. 1,498 / 282 /
138 req/s on one dedicated core. Three traps the sweep found: **more workers made
`/bekendmakingen` slower** (142 on 2 workers, 119 on 6 — twelve hungry processes on eight
cores); **`DB_POOL_MAX_SIZE` is per worker** and times `--workers` it will exceed Postgres's
`max_connections=100`, which collapses the service outright (the pool itself is still not a
bottleneck — it never held more than 15 connections); and **the first run after a restart
reads ~3x low** (268 req/s cold against 782/837/844 warm), so discard it.

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
UPSTASH_REDIS_REST_URL=        # optional; omit to disable caching (degrades silently).
UPSTASH_REDIS_REST_TOKEN=      # COMMENTED OUT locally 2026-10-29: the instance is gone
                               # (NXDOMAIN), so every call threw and the cache could never
                               # hit. Unset costs 24% less CPU per request than configured-
                               # and-failing, and buys zero throughput — see docs/monitoring.md.
                               # The Vercel env that still had it set is no longer a deployment
                               # target; a deploy stack gets its own env file (deploy/env.example).
```

### Deployment

**This is the only deployment path.** Vercel was dropped 2026-11-12: the architecture had
outgrown a single serverless function — Postgres + PostGIS with hand-written SQL, a 512k-row
`poi` table, pgvector embeddings, a self-hosted ORS container and a Prometheus surface are none
of them things a per-invocation process can hold. `vercel.json` and the unused `mangum` pin are
gone with it.

`docker-compose.yml` is the DEVELOPMENT stack: Postgres in a container, API and frontend
on the host via `scripts/dev.sh`, Postgres parameters deliberately small so bloat and
buffer reads show up at 10-20M rows. `docker-compose.deploy.yml` is the deployed one:
API and frontend containerised too, its own project name (`mapscale-deploy`) and its own
volumes, so both can run side by side.

```bash
./scripts/package.sh                 # dist/mapscale-bundle-<date>.tar.gz: source + 35 MB dump
./scripts/package.sh --with-extract  # also the 181 MB NH extract, for self-hosted ORS
# on the target:
cp deploy/env.example .env && docker compose -f docker-compose.deploy.yml up -d --build
```

Full operator notes, including the three routing options and what each missing key
degrades: `deploy/README.md`. Measured: 37 s cold start (22 s of it restoring the dump),
16 s warm, 914 MB of images, 335 MB of restored database.

It ships SOURCE, not images — `docker save` archives are locked to the architecture that
built them, and all three base images are multi-arch, so building on the target is the
thing that works on an arm64 laptop and an amd64 server alike.

Two healthcheck bugs were measured and fixed while building it, both the same shape:
**the probe answered from a different surface than the real client.**
  - `pg_isready` with no `-h` probes the UNIX SOCKET. During initdb the entrypoint runs a
    temporary server with `listen_addresses=''`, so the socket says "accepting
    connections" while TCP is not listening at all. Compose called Postgres healthy 22 s
    before the dump finished restoring, started `api`, and asyncpg exited 1 on
    ECONNREFUSED. The deploy stack now probes `-h 127.0.0.1`. **`docker-compose.yml` still
    has the socket-only probe** — harmless there only because nothing restores a dump
    during its initdb, so the window is ~1 s instead of 22.
  - busybox `wget http://localhost/` resolves `::1` first and `listen 80` is IPv4-only, so
    nginx reported `unhealthy` while its own access log showed 200s. Probe `127.0.0.1`.

Running it on THIS machine needs `--env-file`: `docker compose -f
docker-compose.deploy.yml` reads `./.env` for substitution, and this repo's `.env` points
`ORS_BASE_URL` at the host's `:8080`, which inside a container is the container.
`deploy/api-entrypoint.sh` warns at boot when it sees a localhost URL, because the symptom
otherwise is a working map with a silently wrong catchment.

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

`deploy/nginx.conf` (deployed):
- `/api/main/` → `http://api:8000` on the compose network (prefix forwarded as-is)
- `/api/ai/(.*)` → `https://mapscale-ai-service.vercel.app/api/$1` — the sibling AI service is
  a separate repository and is **still on Vercel**; dropping Vercel here did not move it
- `/assets/` → immutable, 1-year cache
- everything else → `try_files ... /index.html` (SPA fallback)
- no `/metrics` location, deliberately: see the monitoring note above

### Backend layout
```
api/main/
  app.py          FastAPI instance, middleware, exception handlers, router registration
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
as `cache.py` degrades when Upstash is unconfigured. The reason used to be that Vercel had
no Postgres; the deploy stack ships one, so the guard is now about running the API against a
partial environment rather than about a deployment that cannot have a database. Current tables: `municipality` (stage 0); `etl_run`,
`poi_category`, `poi_category_rule`, `poi`, `poi_import` (stage 1); `bekendmaking`,
`bekendmaking_locatie` (official announcements, loaded by `scripts/import_bekendmaking.py`); `claim`, `evidence`, `verdict` (0021-0022 — the measurement ledger, see below; nothing in `api/` reads them). `geocode_cache` is also
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
task and fails the other request too. This is per-process, so it does not help across
`--workers` or across replicas — see `docs/ors-self-host.md` for the structural fix.

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
a publication carries **1.72** geometries on average — 160,477 `bekendmaking_locatie` rows
over the 93,123 publications that have any, measured 2026-11-12. **The 2.5 this file carried
until then was measured at 10,342 building permits, before the national import took the corpus
to 94,183; a measured result has a scale of validity and that one was never re-measured when
the scale moved 9x.** `DISTINCT ON` is still required — only the average moved. Measured in
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

## Measurement discipline (how a number becomes durable here)

This project's expensive failures were never missing features. They were conclusions that were
wrong while looking right: an HTTP 200 from a stranded graph fragment, a load generator that was
itself the bottleneck, a row count that could not see the field it never parsed, a mechanism
story typed into a comment before anything was measured. Two rules are load-bearing.

### 1. Run the `falsifier` agent before a measured conclusion becomes durable

Before any of:

- writing a finding into `docs/pg-notes/`
- an `experiment(...)` / `perf(...)` / `fix(...)` commit that cites a number
- changing a threshold, index, or config on the strength of a measurement
- telling the user "X is N times faster" or "Y is broken"

spawn `falsifier` (defined in `.claude/agents/falsifier.md`, which also carries the full
catalogue of mistake families this project has actually paid for) and give it four things:

1. the conclusion in one sentence
2. the exact commands that produced it
3. the paths to the raw output
4. the axis you believe you varied

It returns `DESTROYED | SURVIVED | REFUSED`, its objections with the commands and bytes behind
each one, and a `SCOPE` line: the narrowest form of the claim its evidence does not break.
**Paste that `SCOPE` line into the notes or the commit message, not your original wording.**

It refuses rather than guesses when any of the four inputs is missing, and that refusal is the
feature — not being able to state what you measured means there is nothing to record yet.

### 1b. Record the claim, its evidence and its verdict

`docs/pg-notes/` is for a human reader and cannot answer "which of these numbers is still
true". The `claim` / `evidence` / `verdict` tables (migration 0021) can:

- `claim.context` is **jsonb on purpose** — it holds the measurement's own dimensions
  (`corpus_size`, `graph_date`, `coordinate`, `n_responses`, whatever was actually varied).
  A generic schema would store only command, path and timestamp, and drop exactly the fields
  that decide when a claim expires.
- `evidence` refuses a row that names a file without a `sha256`. Scratch files get overwritten;
  a path with no digest still points at something afterwards, so the record reads as supported
  when the bytes are gone.
- `verdict.scope` is the deliverable — the narrowest surviving form, the line that gets pasted
  into the notes instead of the original wording. `DESTROYED`/`SURVIVED` must carry one.

The query worth running:

```sql
SELECT * FROM claim_stale(jsonb_build_object(
    'corpus_size', (SELECT count(*) FROM bekendmaking),
    'graph_date',  '<from /ors/v2/status>'));
```

It knows nothing about what the keys mean — any recorded dimension whose value has since
changed is reported, so it catches the next stale number and not only the two already known.
Verified on this session's own claims: it flags `2.5 geometries per publication` (measured at
10,342 rows, now 94,183) and leaves the two that are still current alone. A JSON `null` on
either side means "not measured" and never expires a claim (0022, after the first run produced
one false positive out of three rows).

### 2. Every issue found while working gets an explicit disposition

Noticing something broken and quietly moving on is not allowed. Surface it and decide out loud:
fix now / fix in the next commit / defer to phase N. Deferring is a fine answer; silence is not.
Anything deferred goes into `## Known Issues` below **with an owner phase**, so that section
stays the single place that knows what is outstanding.

---

## Enforced rules: the guard hook

Everything in this file is advice. Claude reads it and mostly complies — but "mostly" is what
let `git push origin --delete` remove 13 remote branches in one call with nothing asking, and
what lets a `httpx.AsyncClient(...)` reappear in a request path after the comment saying not to.

`.claude/hooks/guard.py` is a `PreToolUse` hook, wired up in `.claude/settings.json` on the
`Bash|Write|Edit` matcher. The harness runs it, not Claude, so it is the only part of this file
that is **deterministic** rather than probabilistic. What it decides:

| call | decision | why |
|---|---|---|
| `git push … --delete` / `… :branch` | **ask** | irreversible for anyone who has not fetched, and it closes the PR whose head it is |
| `git push --force` (not `--force-with-lease`) | **deny** | rewrites published history |
| `docker compose … down -v` / `--volumes` | **ask** | the one command that really costs 40 minutes of ORS graph rebuilding |
| `git reset --hard` / `git clean -f…` | **ask** | discards uncommitted work |
| Write/Edit an existing `migrations/NNNN_*.sql` | **deny** | forward-only, no `down` — editing an applied file leaves databases in a state no file describes |
| `httpx.AsyncClient(` under `api/main/` (except `http_client.py`) | **deny** | measured 22.3 → 7.5 ms CPU/request, 40.9 → 119.4 req/s |
| an Edit to `db.py` that drops `plan_cache_mode` | **ask** | without it the same query took 2.4–3.1 s five times and 104 s on the sixth |

Three properties it is built to have, each because the alternative failed in testing:

- **Silence means "not my business".** When no rule matches it prints nothing. Emitting
  `permissionDecision: "allow"` would bypass the normal permission flow and make the guard a
  back door.
- **A command that only MENTIONS a blocked command is not blocked.** The first version denied
  `grep -rn 'git push --force' docs/`. Patterns now require command position — line start, or
  after `;` `&&` `||` `|`, with `sudo`/`env`/`time` prefixes allowed.
- **It fails open, but never silently.** A bug inside the guard prints a `systemMessage` saying
  the checks were skipped, and lets the call through. A guard that quietly stops enforcing is
  worse than no guard, because the rules still look live — the same shape as the empty-200
  problem recorded above. (This was not hypothetical: an undefined name made every path rule
  silently pass, and only a cwd-independence test caught it.)

- **A heredoc body is data, not commands.** The guard blocked its own commit: the message,
  delivered through `git commit -F - <<'MSG' … MSG`, quoted a blocked command as an example,
  and the body is part of the Bash tool's `command` string. Bodies are now stripped before
  matching — except where the command executes its stdin (`bash`, `sh`, `zsh`, `eval`), where
  the body really is commands.

Both of those false positives were found by the guard stopping work that was legitimate, not
by a test written in advance. The second one took a third fix with it: `_POS` anchored on `^`
with no `re.MULTILINE`, so in a multi-line command **every line after the first was invisible**
— which silently made the `bash <<EOF` exception do nothing. A newline now counts as a command
separator. Tests: `python3 .claude/hooks/test_guard.py`, 37 cases, and the must-NOT-fire half
is the half that matters — a guard with false positives gets switched off, and then it protects
nothing.

Adding a rule means adding a pattern plus both tests — one that must fire and one that must not.
To turn the whole thing off, delete the `hooks` block from `.claude/settings.json`.

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
  owed. **Its design was wrong and is corrected here (2026-11-12): it must key on
  `properties.center`, the snapped start node, NOT on `reachfactor`.** Measured over 131
  isochrone responses: healthy driving-car `reachfactor` is 0.0010–0.0993 (0 of 30 rows above
  0.15) while the stranded Utrecht fragment reaches 0.2305, and at driving-car 300 s the
  fragment (0.0159) outranks healthy Amsterdam (0.0034–0.012) — the ordering inverts, so no
  threshold in either direction works, per profile or globally. A `rf > 0.15` gate would degrade
  32 of 83 healthy isochrones to a circle, all 30 driving-car rows among them. Snap distance
  separates cleanly instead: 0.8–49.4 m for all 83 healthy rows against 8,916–9,426 m for all 48
  Utrecht rows, a 191x gap with no overlap. It is also free — `center` is already present in the
  response under the exact body `upstream/ors.py` sends today, where `reachfactor` is absent and
  would need a new request parameter.
  `foot-walking` is a different fault: the public instance black-holes it entirely, while a
  self-hosted instance serves it identically to `foot-hiking` (1.489 km², `reachfactor` 1.0,
  routes 731 m), so that one is in the deployment, not in ORS or a graph. `walking` stays
  mapped to `foot-hiking` regardless, and dropping Vercel did not change that: `ORS_BASE_URL`
  is an env var but `MODE_MAPPING` is one global constant, so it cannot follow the base URL, and
  a deployment may still point at the public host (`deploy/README.md` offers three routing
  options). foot-hiking works against both; foot-walking only against a self-hosted instance. Both profiles are still current in the
  official spec, so neither is a deprecation. Full evidence and the narrowing steps:
  `docs/pg-notes/ors-profile-outage.md`.
- The official ORS OpenAPI spec is at `https://docs.openrouteservice.org/openrouteservice/v2/docs`
  (every `…/api-docs` URL 404s **on the public gateway** — but not on our own instance:
  `http://localhost:8080/ors/v2/api-docs` answers 200 with 93,517 bytes of OpenAPI, measured
  2026-11-12, and it documents `reachfactor` as "the ratio between the area of a circle with a
  radius based on the average speed and the area of the isochrone polygon", range 0–1. Believing
  the blanket "404s" is what sent a whole round of reverse-engineering after a formula the vendor
  ships on localhost). The `/dev/` docs page is a JS SPA that fetches it. It
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
- CI only lints `api/`, so violations elsewhere are invisible: `ruff check tests/` reports 3
  (two unused `pytest` imports in `tests/integration/`, one `E741` in `test_search_logic.py`)
  and **`ruff check scripts/` reports 31, all in `scripts/experiments/`** (measured
  2026-11-12). `api/`, `scripts/import_bekendmaking_body.py` and every
  `scripts/experiments/tussenkop_*.py` are clean.
- No test coverage for any error path (502/504 handling). Removing the housing subsystem also
  removed `tests/unit/test_geocode_key.py`, which was 16 of the 25 unit tests — `pytest
  tests/unit` is now 9, all of them `test_search_logic.py`.
- **`tussenkop` was dropped from every stored body until 2026-11-12, and the deferral that
  allowed it rested on three wrong numbers.** The old entry read "6.3% of body text … those
  headings are appeal boilerplate … switching costs 94,183 fetches". Measured: the loss is
  **0.60–0.69%** of stored body characters (plain random n=400 twice, bootstrap 95% CI
  0.579–0.727%); "appeal boilerplate" is an **Amsterdam-only** finding (Amsterdam carries a
  heading in 63.0% of documents against 5.8% elsewhere, and its 491 headings are 3 distinct
  texts) while a 294-gemeente sweep found 37 distinct heading texts in 18 municipalities,
  including `Datum evenement` and `Datum besluit`; and `gmb-2026-209074` (Nissewaard) held its
  **entire event period only in headings** while the row read `status='no_period'` with NULL
  dates. Now fixed — ElementTree, headings interleaved in document order, and Dutch month-name
  dates (`18 mei 2026`, which the numeric-only pattern could never match). Verified: 122 rows
  that already parsed are unchanged to the date, and an exhaustive Nissewaard+Rhenen re-parse
  gains **1 period in 89 rows**. Full write-up, including two conclusions that were wrong first:
  `docs/pg-notes/bekendmaking-tussenkop.md`.
- **The period label vocabulary is Amsterdam's.** `Looptijd` / `Datum van` / `Datum t/m` /
  `Tijd van` / `Datum en tijd` miss `Datum evenement`, `Soort procedure`, `Verzenddatum` and the
  other heading texts the sweep found. Widening needs a per-publisher survey, not a guess —
  **owner: unassigned.** Related: times in recovered rows stay NULL (`15.30 – 19:00 uur` carries
  no `Tijd van` keyword), which is correct under "nothing is inferred" but recoverable.
- **A full `--refetch` of all 94,183 bodies is owed but not urgent.** Justified by the 0.6% of
  text and the recovered labels, both of which reach the FTS index and the embeddings; NOT
  justified by the period payoff (~1 row in 89). It rewrites every row, which makes that run's
  `last_changed_run` useless as an upstream-change signal — the same cost already recorded for
  the mixed-`raw`-format deferral.
- **The sibling `mapscale-ai-service` is still on Vercel.** `/api/ai/*` proxies to it from both
  `frontend/vite.config.js` and `deploy/nginx.conf`, so the AI analysis panel still depends on a
  Vercel deployment — in a different repository. Dropping Vercel here did not move it, and
  moving it is a change over there. **Owner: unassigned.**
- **Two remote branches are dead after this change**, both carrying a single Vercel-only commit:
  `vercel/install-vercel-web-analytics-1f9fmn` (adds `@vercel/analytics` to the frontend) and
  `feature/housing-layer` (`fix: move curl_cffi to requirements.txt for Vercel deployment`,
  while the housing subsystem itself was removed 2026-10-28). Eight further remote branches are
  0 commits ahead of `main` and fully merged. None has been deleted — **owner: unassigned.**
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
  design, which is why nothing fails. **Commented out of `.env` 2026-10-29** so the failing
  path is not even entered locally. The Vercel env that still had it set is no longer a
  deployment target. If caching is ever wanted again, point these two variables at a live
  instance — no code change is needed.
- `docs/data-source-feasibility.md` and the housing sections of the two refactor plans were
  deleted. `docs/pg-notes/` never mentioned housing and is untouched.
