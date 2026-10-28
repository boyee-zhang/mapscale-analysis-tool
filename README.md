# MapScale Analysis Tool

A spatial data platform for the Netherlands. The user-facing part is a map — travel-time
isochrones, POI density, address search — but the work this repo is
actually about is the layer underneath it: a PostgreSQL 16 / PostGIS 3.4 database with
hand-written SQL, an offline OSM import pipeline, and index designs justified by measured
`EXPLAIN (ANALYZE, BUFFERS)` output rather than by intuition.

The project started as a front-end accessibility demo (isochrone + two POI categories,
point-in-polygon computed in the browser with Turf.js). That framing is gone: the spatial work
moved into Postgres, the POI table holds all 512,844 amenities in the Netherlands, and the
front end renders what the database returns. Raw measurements from each step are archived in
[`docs/pg-notes/`](docs/pg-notes/) — they are the actual deliverable of the current phase.

![Driving isochrone](./docs/ScreenshotDriving.png)
![Walking isochrone](./docs/ScreenshotWalking.png)

---

## What runs where — the live site is **not** this branch

This matters before anything else, because the deployed app and a local checkout behave
differently on purpose.

| | Vercel (production) | Local (`./scripts/dev.sh`) |
|---|---|---|
| Code | `main` | `refactor/modular-architecture` (ahead of `main`) |
| Backend | one Mangum-wrapped serverless function | `uvicorn --reload` on `:8000` |
| Postgres / PostGIS | **none** | Docker `imresamu/postgis:16-3.4`, 512,844 POI |
| `/pois` | Overpass, node-only circle | `poi` table, clipped to the real isochrone polygon |
| Elasticsearch | none — `/search` is PDOK → Nominatim | optional, `docker compose up -d elasticsearch` |
| OpenRouteService | public API, **50 req/day/endpoint** | same, or self-hosted on `:8080` |
| AI service | `mapscale-ai-service.vercel.app` | same (proxied, not run locally) |

Two separate reasons for the gap:

1. **Branch.** Production serves `main`, which predates the `domains/` refactor and all of the
   Postgres work. Nothing on the live site touches a database.
2. **Infrastructure.** Even once that branch merges, Vercel is serverless and the Postgres
   instance is a local Docker container with no public address. So `DATABASE_URL` stays unset in
   production and every DB-backed path degrades instead of failing — `db.enabled()` is the guard,
   and the pre-Postgres code path is deliberately kept alive behind it. Same contract as
   `cache.py` when Upstash is unconfigured.

A consequence worth naming: the in-process single-flight that protects the ORS quota is
per-process, so it does nothing across Vercel instances. See
[`docs/ors-self-host.md`](docs/ors-self-host.md).

---

## Stack

- **Database** — PostgreSQL 16 + PostGIS 3.4, `asyncpg`, hand-written SQL, **no ORM**. Numbered
  forward-only `.sql` migrations with a self-written runner (`scripts/migrate.py`).
- **Backend** — FastAPI (Python 3.12), async HTTPX, JSON structured logging with a per-request
  `X-Request-ID`. Deployed to Vercel through Mangum.
- **Frontend** — React 18 + Vite 6, MapLibre GL 5, Recharts, Axios. No spatial library: Turf.js
  was removed from the code *and* from `package.json` when `/pois` started clipping in Postgres.
- **Cache** — Upstash Redis (REST), demoted to a pure TTL cache.
- **Search** — Elasticsearch 8.14, optional.
- **Upstreams** — OpenRouteService, Overpass, PDOK Locatieserver + WFS, Nominatim, TomTom
  Traffic.

A sibling repo, [`mapscale-ai-service`](https://github.com/boyee-zhang/mapscale-ai-service),
holds a Node serverless ETL over CBS OData and is proxied in as `/api/ai/*`.

---

## Layout

```
api/main/
  app.py            FastAPI instance, middleware, exception handlers
  index.py          re-export for Vercel
  db.py             asyncpg pool on the app lifespan; db.enabled() is the degradation guard
  cache.py          Upstash KV, degrades silently
  upstream/         transport only — ors, overpass, pdok, tomtom, es_client. No business rules.
  domains/
    geo/            spatial primitives: isochrone, POI-inside-a-shape
  routers/          isochrone, pois, directions, region, traffic, search, health
migrations/         0001_init … 0004_poi_category_seed  (+ .noxact.sql for CREATE INDEX CONCURRENTLY)
scripts/            dev.sh, migrate.py, import_poi.py, seed_municipalities.py, ingest_addresses.py
docs/pg-notes/      raw EXPLAIN / lock / vacuum output per stage
```

The rule the refactor is built on: `upstream/` is transport, domain semantics live in
`domains/<d>/service.py`. If a function in `upstream/` needs to know what an
"isochrone" is, it is in the wrong directory. Full specs in
[`docs/architecture-refactor-plan.md`](docs/architecture-refactor-plan.md) and
[`docs/postgres-refactor-plan.md`](docs/postgres-refactor-plan.md).

---

## Data pipeline

**POI import** (`scripts/import_poi.py`, pyosmium) reads a Geofabrik `.osm.pbf` extract and
upserts into `poi`:

- Full Netherlands: **512,844 POI in 2m53s**. A no-op re-run is `INSERT 0 0, UPDATE 0` in 1m58s.
- Idempotent by `ON CONFLICT DO UPDATE … WHERE <row differs>` plus a staging anti-join. Without
  the `WHERE`, a no-op re-import rewrote every row and doubled the heap.
- Soft delete is confined to the bounding box of what the run staged, so importing one province
  cannot delete another.
- The tag whitelist lives in `poi_category_rule`, so widening the import is an `INSERT`, not a
  code change.
- `etl_run` is the state machine; its partial unique index is predicated on
  `finished_at IS NULL`.

**Municipality polygons** (`scripts/seed_municipalities.py`, ~342 rows from the PDOK CBS WFS)
resolve city names for `/search` and the address ingest: **26 ms from Postgres against 534 ms
through the WFS**. They used to back a housing choropleth, which has been removed; the table
and the measurement stand on their own.

---

## Measured findings

These are the reason the project exists in its current form. Each one is reproducible from
`docs/pg-notes/`.

- **A composite `GIST (category, geom)`** (via `btree_gist`) replaced a separate `GIST (geom)`
  and `btree (category)`.
- **`ST_DWithin(geom::geography, …)` is unindexable** against a `geometry` GiST index. Rewriting
  it as `ST_Buffer(pt::geography, r)::geometry` + `ST_Contains` took the query from **1445
  buffers to 52**.
- **A result has a scale of validity.** At 82,259 rows (Noord-Holland), `category = ANY($1)` fell
  out of the index condition — btree_gist's `amsearcharray` is false — and an `unnest` +
  `CROSS JOIN LATERAL` rewrite with an inner `LIMIT` was 21x better (1708 → 80 buffers). At
  512,844 rows the planner switched to a **Bitmap** Index Scan, which loops the array in the
  executor, and `= ANY` came back *ahead*: **91 buffers against 98**. Same query, same index,
  opposite conclusion at 6x the data. The inner `LIMIT` is still load-bearing.
- **Applying a rule is not the same as it applying.** The `etl_run` partial index was built to
  make its UPDATEs HOT, and 3/3 of the `note`/`row_count` updates are. The terminal one is
  0/1 — it is the UPDATE that writes `finished_at`, the column the index is predicated on.
  (`pg_stat_force_next_flush()` is required between statements, or the counters read 0.)
- **The planner chose GiST over a Seq Scan on a 342-row table** — not for I/O, but because
  PostGIS declares `st_contains` with `procost` 20.66 against 4368.42 for the scan.
- **Single-flight, not caching, is what saved the ORS quota.** The browser issues `/isochrone`
  and `/pois` concurrently with identical parameters, so both miss the cache before either
  writes: 38 ORS calls for 18 unique parameter sets. The free tier is 50 req/day/**endpoint**.
  Both awaits are `asyncio.shield`ed, otherwise one client disconnecting cancels the shared task
  and fails the other request.

---

## Endpoints

All under `/api/main`.

| Endpoint | Source | Cache |
|---|---|---|
| `/health` | — | — |
| `/isochrone` | ORS | 24h |
| `/pois` | `poi` table (Overpass fallback) | 6h on the Overpass path only |
| `/region` | PDOK reverse | 7d |
| `/directions`, `/bulk_directions` | ORS | — |
| `/traffic/tile-url`, `/traffic/flow`, `/traffic/incidents` | TomTom | 5m |
| `/search` | ES → PDOK → Nominatim | — |

**Search waterfall.** Elasticsearch `nl_addresses` (weights overridable per request via
`w_street` / `w_city` / `w_postcode` / `w_label`, accepted only above `ES_MIN_SCORE=15`) → PDOK,
filtered so every long query token must appear in the label → Nominatim. Tier 2–3 results are
written back into ES, so the index self-enriches. Without `ES_URL` the chain simply starts at
PDOK.

---

## Getting started

Requirements: Python 3.12, Node 18+, Docker, an OpenRouteService API key.

```bash
git clone https://github.com/boyee-zhang/mapscale-analysis-tool.git
cd mapscale-analysis-tool
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-local.txt
npm --prefix frontend install
```

Create `.env` in the repo root:

```
ORS_API_KEY=                   # required for isochrone + directions
ORS_BASE_URL=                  # optional; defaults to the public host
TOMTOM_API_KEY=                # required for traffic
DATABASE_URL=postgresql://mapscale:mapscale@localhost:5432/mapscale
ES_URL=                        # optional; omit and /search starts at PDOK
UPSTASH_REDIS_REST_URL=        # optional; omit to disable caching
UPSTASH_REDIS_REST_TOKEN=
```

Then one command brings up the whole stack — port pre-check → Postgres (`--wait`) → migrations
→ uvicorn `:8000` + Vite `:5173`, and Ctrl-C stops all of it:

```bash
./scripts/dev.sh
```

It deliberately leaves out two compose services: `ors` (the first start builds three routing
graphs, tens of minutes and several GB) and `elasticsearch` (`/search` already degrades, and
this repo's `esdata` volume is empty). Start either by name when you want it.

### Load the POI table

```bash
# download a Geofabrik extract by hand into the gitignored data/
#   https://download.geofabrik.de/europe/netherlands/
python scripts/import_poi.py data/netherlands-latest.osm.pbf
python scripts/seed_municipalities.py
```

### Optional services

```bash
docker compose up -d elasticsearch     # :9200, then: python scripts/ingest_addresses.py
docker compose up -d ors               # :8080 — first start builds graphs. docs/ors-self-host.md
```

### Lint and test

```bash
ruff check api/                        # line-length 120
pytest tests/unit                      # no server needed
BASE_URL=http://localhost:8000 pytest   # integration tests hit a live server
```

The isochrone and directions tests are marked `requires_ors` and need `ORS_API_KEY` set,
so they are skipped without one. Run them sparingly: the free tier is 50 requests per day
per endpoint, and a full integration pass spends a visible fraction of it.

A note on the container: its memory parameters are deliberately small
(`shared_buffers=128MB`, `work_mem=4MB`) so spills and autovacuum behaviour become observable at
~10M rows instead of ~1B. Do not "fix" them. The image is `imresamu/postgis` rather than
`postgis/postgis` because the latter is amd64-only, and emulating it on Apple Silicon makes
every `EXPLAIN ANALYZE` timing meaningless.

---

## Current limitations

Honest list; what remains is upstream, or missing work rather than a defect.

- **ORS's `cycling-*` graphs are broken upstream and return HTTP 200**, so cycling mode silently
  under-reports — all four profiles answer a 600 s isochrone with the same 0.031 km² blob,
  roughly 400x too small. `MODE_MAPPING["cycling"]` is deliberately *not* re-pointed; every
  cycling graph is equally broken and serving a walking graph for a bike request would be a
  worse lie. `walking` *is* remapped to `foot-hiking`, which is measured-equivalent, because
  `foot-walking` black-holes the request. Evidence:
  [`docs/pg-notes/ors-profile-outage.md`](docs/pg-notes/ors-profile-outage.md).
- No test coverage for any error path. Removing the housing subsystem took
  `tests/unit/test_geocode_key.py` with it — 16 of the 25 unit tests — so `pytest tests/unit`
  is now 9. No rate limiting; upstream quotas are the only backpressure.
- Traffic figures in the AI service are a PV27 (North Holland) provincial proxy applied to every
  municipality, so other provinces are not location-specific.

---

## Roadmap

Numbered by the stages in [`docs/postgres-refactor-plan.md`](docs/postgres-refactor-plan.md),
which is authoritative.

- **Stage 0 — done.** `geocode_cache`, `municipality`, the migration runner, the degradation
  guard. `geocode_cache` is now an orphan: its only caller was the removed housing geocoder, and
  `0001_init.sql` is deliberately left untouched rather than adding a migration to drop it.
- **Stage 1 — done.** Offline OSM POI import, `etl_run`, `/pois` answered from PostGIS.
- **Stage 2.** A transactional outbox plus a `FOR UPDATE SKIP LOCKED` worker. Needs a long-lived
  process, so the backend has to leave Vercel first.
- **Stage 3.** Synthetic snapshots at scale: partitioning, autovacuum and bloat behaviour, tuning.
- **Stage 4.** Adding a geometry column online and backfilling it without blocking reads.
- **Stage 5 — deferred.** PgBouncer and a read replica.

Front-end state management is explicitly deferred: `MapContainer.jsx` is still the store.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
