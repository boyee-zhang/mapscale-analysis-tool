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
`/bulk_directions` 65 (removed 2026-11-14). Carry forward the **per-core** costs, not the peaks: 0.67 / 3.55 /
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
`bekendmaking_locatie` (official announcements, loaded by `scripts/import_bekendmaking.py`),
`bekendmaking_activity_text` (0027 — `activity_text` derived once instead of per read, filled by
`scripts/import_bekendmaking_body.py` in the body's own transaction and by
`scripts/derive_activity.py` for re-derives; `bekendmaking_activity_pending` is what has not been
derived yet), `activity_rule` (0030 — which extraction is current, read by both the pending view
and `scripts/activity_rules.py`; 0031 adds `source_view`, so one row names both the transformation and its input set), `bekendmaking_activity_source` (0031 — the declared input set of the derive, as one fingerprint per publication, over the STORED generated columns `bekendmaking.title_fp` and `bekendmaking_body.body_text_fp`); `claim`, `evidence`, `verdict`, `world_facet`, `world_snapshot`, `verdict_world`, `topic` (0021-0026 — the measurement ledger, see below; nothing in `api/` reads them). `geocode_cache` is also
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
| `/traffic/tile-url` | GET | — (static URL) | — |
| `/traffic/flow` | GET | TomTom Flow | 5m |
| `/traffic/incidents` | GET | TomTom Incidents | 5m |
| `/search` | GET | ES → PDOK → Nominatim | — |
| `/search/notices` | GET | pgvector over `bekendmaking_embedding` + optional isochrone | — |

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

> The design of all of this — why three layers, what each enforces, what is still missing, and
> the principles with the evidence behind them — is in **`docs/ai-dev-framework.md`**. Read it
> before extending any of the mechanisms below. It is also the honest list of gaps. Two it used
> to name are closed: the ledger has a write path (`scripts/claims.py`, §1b) and `status` is
> derived rather than hand-written, so the two rows that contradicted their own verdicts cannot
> recur. What remains open is that nothing *forces* you through that write path — see the last
> paragraph of §1b.

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
true". The ledger can. **`scripts/claims.py` is the only way in**:

```bash
.venv/bin/python scripts/claims.py facets                   # what the world is made of
.venv/bin/python scripts/claims.py snapshot                 # probe the world, record it
.venv/bin/python scripts/claims.py new --topic search --text "..." [--ack 7,9]
.venv/bin/python scripts/claims.py evidence 4 --command "..." --output path/to/raw.json
.venv/bin/python scripts/claims.py verdict 4 --verdict SURVIVED --scope "..." --facets judgeset
.venv/bin/python scripts/claims.py stale                    # THE query worth running
.venv/bin/python scripts/claims.py show [4]
```

The falsifier writes **its own** verdict — pass it the `claim_id` and the three commands.
That is not a convenience: the `SCOPE` line is the deliverable, and routing it through a
paraphrase is the failure this repo already records a rule against.

**The world is a content-addressed snapshot, not a set of keys** (0023). A facet is probed,
the whole probe output is hashed, and a verdict is anchored to the digests it was reached
under (`verdict_world`). Different digest ⇒ untested, and nothing has to know why. This
replaced `claim_stale(jsonb)`, which 0024 drops, for three measured reasons:

- It needed its caller to assemble the current world. Nothing did: **0 callers in 12 days**,
  and the one stale number it existed to catch was found by hand anyway. `verdict_stale()`
  takes no arguments.
- A key can be under-specified and still look fine. Claim #3 recorded
  `graph_date: 2026-10-28T16:45:20Z` — which is **cycling-regular's** build date. The three
  profiles carry three (16:43:51 / 16:44:44 / 16:45:20), so rebuilding only `driving-car`
  left that key matching: "not stale", wrongly. A digest over all three cannot do that.
- "Could not be read" had to be told apart from "has not changed" — the whole of 0022. An
  unreachable ORS made every graph claim read as current. Now unreachability is a payload,
  so it hashes **differently** from an answer: incompleteness is a visible difference, not a
  silent pass.

The cost, stated: a digest is all-or-nothing where a key comparison was precise. One imported
row moves the `corpus` digest and every verdict anchored to it is untested at once. Hence
facets. `claims.py facets` lists them; **`world_facet.default_anchor` decides which ones a
verdict anchors to when told nothing**, and the rule lives in the table so that adding a facet
cannot silently change what every future verdict anchors to.

| facet | kind | anchors by default |
|---|---|---|
| `corpus` | probe in code: row counts + a content digest of `bekendmaking` | **yes** |
| `graph` | probe in code: every profile's `graph_build_date` + `osm_date` | **yes** |
| `code` | probe in code: `HEAD` + `HEAD^{tree}` + a digest of the working tree when dirty | no |
| `judgeset` | **path set**: the frozen judgeset inputs plus the two scripts that score them | no |

**A facet may be defined by a path set** (0025), digested file by file, and that is the right
shape for most measurements: a measurement does not depend on "the repository", it depends on
specific bytes. Declaring one is an INSERT into `world_facet` with `paths`, no code change.
A declared path that is missing is recorded as `ABSENT` rather than skipped — dropping it would
make a facet whose inputs were *deleted* compare equal to one where they are intact.

`corpus` and `graph` are on by default because forgetting them fails toward over-reporting,
which is visible, where omitting them fails toward silence. `code` and every path facet are
**off**, because anchoring to them is a *narrowing* assertion — "this measurement reads only
these bytes" — and a narrowing assertion has to be made on purpose. `--facets none` is legal
and makes a verdict that can never expire, which is why `verdict_unanchored` lists them.

**Do not anchor an ordinary measurement to `code`.** 0023 did, and the result is the clearest
thing this ledger has produced about itself: `probe_code` hashed `dirty_files`, a *count*, so
the digest moved three times in one session while the measurement's frozen input stayed
byte-identical — and then correcting the notes exactly as a verdict's own `SCOPE` instructed is
what expired that verdict. 0025 fixes the probe (a content digest of the tracked diff *and*
every untracked file; `branch` removed, since the same commit under two branch names is the
same code and a `checkout -b` expired everything), but the deeper error was the shape. The
judgeset measurement is anchored to `judgeset` + `corpus`, deliberately not to `code`.

**Only the latest verdict on a claim can be news** (0025). `verdict_stale()` and
`verdict_unanchored` both read `verdict_latest`. 0023's versions reported every verdict ever
recorded, so a superseded one stayed in the report with no way to clear it — and an entry
nobody can act on trains the reader to skim, which is how the one real entry gets skimmed with
it. The older verdict is still in `verdict` and still joinable; it is just not something to
act on.

**`claim.status` is not a column** (0024). It was one, with a `DEFAULT 'open'` and a CHECK
over four values, and nothing ever wrote it — the three rows backfilled by hand all said
`survived` while two had a latest verdict of `DESTROYED`, and `claim_stale` filtered on
`status IN ('open','survived')`, so **both refuted claims were still being checked as live**.
A ledger built to stop a wrong conclusion from looking right was itself a wrong conclusion
that looked right, in two rows out of three. It is now derived in `claim_current`; a trigger
would have been weaker, because the column would still be there to read. `REFUSED` maps to
`open`: the falsifier refuses when its inputs are missing, so nothing was tested.

Three more constraints exist because the alternative lies: `evidence` refuses a path without
a `sha256` (scratch files get overwritten; a path with no digest still points at something
afterwards, so the record reads as supported when the bytes are gone — and `claims.py`
hashes the file rather than taking your word for the digest); `verdict.scope` is required
unless `REFUSED`; `topic` and `world_facet` are **closed sets with FKs**, because free text
lets `search` and `Search` diverge and then a per-topic staleness check returns zero rows,
which reads exactly like "nothing is stale".

`claim.context` stays jsonb and keeps its gin index, but it now holds the **experiment's**
parameters only (`queries`, `bootstrap_resamples`, `method`). The world's dimensions moved
to facets. Claim #2 had `corpus_size` (world) sitting next to `method` (experiment); that
conflation is why `claim_stale` had to guess with `now_ctx ? k`.

Verify the schema against a live database with
`.venv/bin/python -I scripts/verify_ledger.py` — 17 checks, every one in a rolled-back
transaction, and it reads **both** streams because psql reports a constraint rejection on
stderr and reading only stdout once made a correct rejection look like a failed test.

Its must-reject half has already paid for itself twice. 0025's `world_facet_paths_nonempty`
**did not reject an empty path set**: `array_length(ARRAY[]::text[], 1)` is `NULL`, not `0`,
`NULL >= 1` is `NULL`, and **a CHECK passes on NULL**. An empty path facet is the worst row
this schema can hold — `probe_paths([])` returns a constant, so its digest never moves, so a
verdict anchored to it never reports stale. 0026 fixes it with a `coalesce`. Two bugs here now
come from Postgres's three-valued logic (this one and 0022's `jsonb null IS DISTINCT FROM`), so:
**any sub-expression in a CHECK that can go NULL is a hole, not a rejection.**

**What is still advisory.** Nothing forces you through `claims.py`. The hook that would
(`INSERT INTO claim|verdict` outside it → deny) is not written, nor is the `Stop` gate that
would refuse to let a session end with an open claim — by decision, not oversight: `Stop`
fires at the end of **every turn**, not of the session, so that gate as first designed would
have held every later turn hostage, and a block an agent cannot satisfy (Postgres down) is a
hang. Build those against evidence from walking the loop, not ahead of it.

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
| a `shard-judge` spawned with no fan-out plan in its prompt | **ask** | every extra agent is ~71,600 tokens for wall-clock time alone — see below |

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

## Fanning out: the measured economics

`scripts/fanout.py` decides how many agents a judgement needs. The answer is almost always one,
and the reason is counter-intuitive enough to be worth the numbers.

Judging 191 `tussenkop` headings on 2026-11-12, five `shard-judge` agents were spawned, one per
`doc_type`. Their measured costs:

```
shard                   units   tokens      ms          tokens ~ 70,609 + 25.3 * units
omgevingsvergunning         3   66,898  14,908          R2 = 0.033
evenementenvergunning       3   68,844  15,461
exploitatievergunning      52   74,975  17,663          An agent judging 3 units cost 66,898.
andere vergunning          92   68,085  23,493          One judging 92 cost 68,085.
verkeersbesluit            41   79,075  23,574          The cost is FIXED, ~71,600 per agent.
                               -------
                               357,877
```

So the waste has two parts, and the obvious one is the small one:

```
all 191 units inline, as payload      ~35,800 tokens
the 8 distinct texts among them           ~30 tokens
actually spent                        357,877 tokens
```

De-duplicating would have saved ~35,800. **Not fanning out would have saved ~286,000** — eight
times more. Fanning out buys wall-clock time, not tokens: those five ran in ~24 s against ~95 s
serial, so it was ~286,000 tokens for 71 seconds.

Hence the order of questions in `plan()`: *can one agent hold this* first, *how much is
repeated* second.

```python
from scripts.fanout import plan
p = plan(units, key=lambda u: u["text"], render=lambda u: u["text"] + u["context"])
print(p.report())          # units / distinct / payload / how many agents / why
for shard in p.shards:     # len(p.shards) == 1 unless the payload really exceeds 100k tokens
    ...
labels = {...}             # one decision per DISTINCT key
results = p.expand(labels) # back onto all 191 rows; raises on a partial answer
```

`expand()` refusing a partial answer matters: a judgement that silently covered only some keys
would expand into a result full of `None` that reads exactly like a complete one.

The gate is in two places so it is not another thing to remember: `shard-judge`'s description
requires `plan.report()` as its fifth input, and the guard hook **asks** when one is spawned
without it. Tests: `tests/unit/test_fanout.py` (14 cases, including the real 191-unit
distribution) and the `Agent` cases in `.claude/hooks/test_guard.py`.

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
- `/traffic/flow` has an `api.js` method but no caller. **`/bulk_directions` was removed
  2026-11-14**: it had no caller in `frontend/src/` at all, and an unbounded `targets` list
  made one unauthenticated request able to exhaust the 50/day ORS `/directions` quota. The
  idea it was for — real network distance to every POI in the isochrone, rather than the
  straight-line filter — is still worth having; it needs a cap and a caller, not a revert.
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
- **Fixed 2026-11-14 in 0027, and the diagnosis is worth more than the fix.** `/search/notices`
  spent 1,778–2,040 ms warm in one query — 95% of the endpoint — and the HNSW scan inside it was
  **0.76–1.13 ms**. The planner put `bekendmaking_activity` on the *probe* side of a hash built
  from the already-LIMITed ranked rows, so all 94,183 view rows were projected to decorate 20.
  `service.py`'s own comment said that could not happen: **a CTE plus a LIMIT expresses an
  intention, it does not constrain the planner.** Controls (falsifier verdict 14 on ledger claim
  17): the same join with no view expression evaluated is 9–12 ms, so it was the projection, not
  the join or the I/O; cost is per *referenced view column*, not per row read — adding
  `activity_src` doubled it; and `body_text` was detoasted **8 times per row** (661,518 buffer
  hits = 8 × 83,289), of which the six regexes are ~61% and TOAST decompression ~29%, so no
  cleverer regex could go below the detoast floor. Invariant over limit 1/5/20/50, a `doc_type`
  filter, a one-week window, a Dutch query and the isochrone branch — a query returning 1 row
  cost what one returning 50 did. Fixed **upstream, not in the SQL**: 0027 stores the text.
  Same query now 1.9–5.7 ms, view output byte-identical (md5
  `c221dcb413bbc2f90d601eefa53246be`). `scripts/verify_plans.py` asserts the plan so the next
  regression is loud. **The fix's own numbers have not been through `falsifier`** — ledger claim
  18, `falsified: false`.
- `background_similarity` is now the largest SQL term in that endpoint at 29–38 ms. Not a
  problem; recorded so the next person does not re-measure the thing that was already fixed.
- **0027 was incomplete, and 0029/0030 are the three fixes.** `falsifier` DESTROYED claim 18
  (verdict 15) and found two real defects plus a wrong number:
  - **Nothing called the derive step.** 0027 backfilled inside the migration to avoid a window
    where search returns `''`; the window was not closed, it was moved to every future import.
    `scripts/import_bekendmaking_body.py` now derives inside the same transaction as the body
    upsert, so a committed body always has committed text, and it reports `derived=` and
    `pending_after=` in the `etl_run` note — a run that stored bodies and derived nothing used
    to read as a success.
  - **`''` meant two things.** It is 0015's deliberate sentinel for "no body and no title" *and*
    was "not derived yet". 0029 adds `activity_derived` to the read view; `embed_bekendmaking.py`
    now tests it explicitly and warns rather than silently under-indexing.
  - **The rule version was in two places** — `rule_version <> 1` in the pending view and
    `RULE_VERSION = 1` in Python. The first bump would have made the view report every row
    forever while the script reported none. 0030 adds `activity_rule` (one current row, enforced
    by a partial unique index) plus `activity_rule_current()`; both sides read it. **It is not a
    schema version** — `schema_migrations` says which DDL ran, `activity_rule` says which
    transformation produced the data.
  - **The number was corpus-branch-only.** 1.9–5.7 ms is the corpus-wide branch; the
    **isochrone** branch — what a map click triggers — is **43.7–67.6 ms**, a median **41x**,
    not 533x. The remainder is the spatial CTE (13 ms) and a seq scan hashing all 94,183 vectors
    (22.6 ms), neither of them the view. Carry 41x.
  - Verified by `.venv/bin/python -I scripts/verify_activity.py` — 15 cases, each in a
    rolled-back transaction, including the controls that make the checks non-vacuous.
- **`probe_corpus` now covers derived state, and widening it expired every `corpus`-anchored
  verdict once.** It read only `bekendmaking` and `bekendmaking_locatie`, so emptying
  `bekendmaking_activity_text` left its digest byte-identical while every publication's
  `activity_text` became `''` — a measurement's premise destroyed with the verdict still reading
  "current". It now carries `activity_count`, `activity_digest` (identifier + `rule_version` +
  content, ordered by the primary key) and `activity_pending_count`. The one-time invalidation
  that followed is the cost 0025 already records for widening a snapshot, and it is honest:
  claim 17 says "the ~2.0 s is X" and there is no ~2.0 s any more, so stale is the correct
  reading. **Owner: unassigned** — re-validating claim 17 means re-measuring something that no
  longer exists, so the likelier disposition is to supersede it with claim 18's successor. Note what the probe
  still cannot see on its own: it detects a title change only because `activity_pending_count`
  is sourced from the pending view, so the view being right is what makes the probe right —
  asserted in `a_title_change_moves_the_corpus_probe` rather than assumed.
- **Fixed 2026-11-14 in 0031/0032: staleness is a fingerprint over the declared input set, not
  a list of watched fields.** `falsifier` DESTROYED claim 23 (verdict 16) because
  `bekendmaking_activity_derive` reads `bb.body_text` **and `b.title`** while only the body was
  watched — and **32,329 of 94,183 rows (34.3%) derive their `activity_text` from the title
  alone**, with `scripts/import_bekendmaking.py` upserting `ON CONFLICT … SET title =
  EXCLUDED.title`, a branch **108 rows had already taken**. A title change left all three
  surfaces reporting clean at once: pending 0, the importer's `pending_after=0`, and the
  `corpus` digest byte-identical (`c9849a28c6e31295 -> c9849a28c6e31295`).
  **The obvious patch was rejected.** Comparing `bekendmaking.last_changed_run` would have been
  a third hand-maintained sentinel that fails identically the next time the extraction reads
  something new. 0031 instead declares the input set as a view,
  `bekendmaking_activity_source`, and stores its `source_fp` on the derived row. Adding an
  input changes that view, which changes every fingerprint, which makes every row pending —
  there is nothing left to remember.
  Three things make it hold rather than merely be intended:
  - **The inputs hash themselves.** `title_fp` and `body_text_fp` are STORED generated columns
    (`md5(text)` is IMMUTABLE), so no writer can forget to maintain them, and the pending check
    compares two 32-byte strings instead of detoasting `body_text` — which would have
    reintroduced 29% of the cost 0027 removed.
  - **`scripts/verify_activity.py` asserts the input set from the catalog**, not from a list in
    the file: `pg_depend` gives the columns the derive view actually reads, the generation
    expressions give the `title → title_fp` correspondence, and the case fails if the derive
    grows an input the fingerprint does not hash. Its must-fail control injects
    `b.abstract` into the derive view and asserts the check refuses — 0026 is in this repo
    because 0025 shipped a CHECK that could not reject anything.
  - **NULL means pending, not fresh.** 0031's backfill records a fingerprint only for rows
    whose stored text still equals what the current inputs produce; anything else keeps NULL
    and stays pending. Backfilling unconditionally would have declared every already-stale row
    fresh, in the migration that exists to fix that. Measured: all 94,183 rows matched, 0 left
    NULL.
  Verified: 24 cases in `scripts/verify_activity.py`, an empty-database apply of 0001..0032,
  and a real `import_bekendmaking.py` + `import_bekendmaking_body.py` run on a fresh database
  where a title change on an imported row reports `why=source_changed`. The read view's output
  is unchanged (md5 `c221dcb413bbc2f90d601eefa53246be`).
  **Two costs, stated.** A fingerprint over inputs over-reports: a title change on a row whose
  output cannot depend on the title still goes pending, converging on the next derive — the
  visible direction, which this file requires. And `body_etl_run` leaves the staleness test, so
  a re-fetch returning byte-identical text no longer re-derives; that makes the owed full
  `--refetch` much cheaper.
  **`falsifier` DESTROYED claim 27 (verdict 17) and the headline above is half false.** What
  survived: the fingerprint is correct for the two columns it hashes — a title change, a body
  change, a NULL `source_fp` and a rule bump each report pending, an identical-byte refetch
  does not, the view and `activity_rules.PENDING` agree row-for-row (0/0 clean, 94,184/94,184
  after a bump), and the three body states give three distinct fingerprints. What died: "an
  input added without being fingerprinted fails `verify_activity.py`". The structural check's
  catalog query filters `d.refobjsubid > 0` and hardcodes `relname IN ('bekendmaking',
  'bekendmaking_body')`, so an input read through a whole-row reference
  (`row_to_json(b.*)->>'abstract'`, 15,843 rows stale) or from a third table
  (`bekendmaking_locatie.straatnaam`, 8,273 rows stale) goes unwatched with all 24 cases
  green — the must-fail control uses the same column by a different syntax, so it tests the
  syntax, not the property. Staleness also does not cover the *extraction* (replacing the
  derive view with no `activity_rule` bump: 85,936 rows stale, corpus digest byte-identical),
  nor the source view's totality (filtering out one gemeente hides 53,727 publications from
  the view, from `PENDING` and from `probe_corpus` at once). **And 0032's plan numbers do not
  reproduce**: the 139,640-buffer four-seq-scan plan appears only with
  `enable_indexonlyscan=off`, i.e. the unvacuumed state 0032's own parenthesis documents for
  the *rejected* indexes — vacuumed, the redundant driver is an Index Only Scan of 672 buffers
  (0.5%), medians are 503.7 vs 442.9 ms with 0031 faster in 3 of 9 paired reps, and 407–413 ms
  was never reached. Direction holds, mechanism and magnitude do not; there is also no raw
  output for that measurement in `docs/pg-notes/raw/`. Full objections:
  `docs/pg-notes/raw/activity-fingerprint-falsify.txt`. **The dispositions are not written yet
  — owner: unassigned.**
- **The in-transaction derive is true but asserted by nothing.** Moving the `derive()` call one
  dedent **outside** `async with conn.transaction():` in `scripts/import_bekendmaking_body.py`
  reintroduces exactly the defect verdict 15 named, and **all six cited commands stay green** —
  ruff, `verify_activity.py`, `verify_ledger.py`, `pytest tests/unit`, `test_guard.py`. The 15 cases
  call `scripts.activity_rules.derive` directly, so they test `derive`, not the call site; *deleting*
  the call is caught only by ruff F401/F841 on that file, and `ci.yml` runs `ruff check api/` only.
  The property does hold today — a real `import_bekendmaking_body.main()` against a fresh 0001..0030
  database gives `pubs=3 bodies=3 act=3 pending=0`, and with the derive view renamed away the two
  fetched bodies are **not** committed and the `etl_run` reads `failed`. **Owner: unassigned.**
- **The importer never `REFRESH`es `bekendmaking_search_text`**, so a committed derived row is absent
  from the FTS index (`fts_rows=0` after a 3-body import on a fresh database) while
  `pending_after=0` reports success. 0027 backfilled in-migration precisely to avoid that window;
  its second half is still open for every row the importer stores. Bounded: no request path reads
  that matview — the only readers are `scripts/experiments/judgeset_pool.py` and the two refreshers —
  so this hits the FTS/judgeset experiments, not `/search/notices`. **Owner: unassigned.**
- **The `activity_derive` path facet (0028) does not digest the derivation.** It lists 0015, 0027,
  `service.py` and `derive_activity.py`, and omits `scripts/activity_rules.py` — which now holds the
  derive SQL, the `PENDING` SQL and `EXTRACTORS` — plus `import_bekendmaking_body.py`, 0029 and 0030.
  Appending a line to `activity_rules.py` leaves the digest at `4a3f89c3a2e9d421`, unchanged. Two of
  its four paths are applied migrations, which are forward-only and guard-denied, so those digests
  can **never** move; and 0027 dropped 0015's view, so that entry anchors a file describing an object
  that no longer exists. The derivation's real definition is a database object, which no path facet
  can see. **`falsifier` reported "no verdict references it, so the cost is prospective" and that is
  wrong** — verdict 15 is anchored to it (`98c3b8c57f01`, now reading
  `98c3b8c57f01 -> 4a3f89c3a2e9`), so one verdict already rests on a path set that cannot see the
  derivation. It expired here only because `derive_activity.py`, which *is* in the set, happened to
  change too. Fixing it is an INSERT into `world_facet`, not an edit to 0028. **Owner: unassigned.**
- **`''` still means two things to three of the four readers of `bekendmaking_activity`.** 0029 added
  `activity_derived` and only `scripts/embed_bekendmaking.py` tests it;
  `api/main/domains/search/service.py`, `scripts/score_noise_embedding.py` and
  `scripts/experiments/noise_{embedding,retrieval}_bench.py` join the view with no such test.
  **Owner: unassigned.**
- **Bumping `activity_rule` with zero data change moves the `corpus` digest**
  (`c9849a28c6e31295 -> 75b77d69d400b8f6`, via `activity_pending_count 0 -> 94,183`;
  `activity_digest` unchanged), so a rule bump expires every corpus-anchored verdict at once.
  Defensible — derived state really is stale then — but `corpus` now conflates data with rule
  configuration. Recorded, not a defect.
- The blocking half of that endpoint is fixed: `encoder.encode()` is a torch forward pass and
  was called directly from `async def`, and the ~440 MB model was loaded inside the first
  request. Measured 2026-11-14: **9.76 s to load plus 925 ms for the first forward pass**, all
  on the event loop with `--workers 1`, i.e. every in-flight request stalled, `/health`
  included. It now loads in the app lifespan (uvicorn does not accept connections until that
  yields, and the deploy healthcheck allows `start_period: 40s`) and runs under
  `asyncio.to_thread`. **The deploy image never hit this**: `sentence-transformers` is only in
  `requirements-local.txt`, so there `available()` is False and the endpoint answers 503 with a
  reason. Shipping the feature means adding the dependency, and then the fix is what makes it
  safe. A sweep of `api/main/` found **no other blocking call on the loop** — no `time.sleep`,
  no `requests`, no sync file I/O, no `subprocess`; `json.dumps` of an isochrone polygon is
  0.020 ms at 33 vertices.

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
