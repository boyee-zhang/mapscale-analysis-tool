# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MapScale is a geographic analysis tool for the Netherlands. It started as an urban accessibility
tool (travel-time isochrones + POI density) and has grown two more subsystems: a three-tier
address search, and a housing-market subsystem (rental listings, choropleth, AI reports).

**Tech Stack:**
- Frontend: React 18 + Vite 6, MapLibre GL 5, Turf.js, Axios, Recharts
- Backend: FastAPI (Python 3.12), HTTPX (async), deployed as a single Vercel serverless function via Mangum
- Database: PostgreSQL 16 + PostGIS 3.4, local Docker, `asyncpg` with hand-written SQL (no ORM)
- Cache: Upstash Redis (REST API)
- Search: Elasticsearch 8.14 (optional — local via Docker, falls back to PDOK/Nominatim)
- AI: DeepSeek (`deepseek-chat`) via the OpenAI-compatible SDK
- External APIs: OpenRouteService, Overpass, PDOK Locatieserver + WFS, Nominatim, TomTom Traffic, Holland2Stay GraphQL

## Development Commands

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
```

Migrations are numbered `.sql` files in `migrations/`, applied forward-only by a
hand-written runner; there is no `down`. A file named `NNNN_x.noxact.sql` runs **outside**
a transaction, which is required for `CREATE INDEX CONCURRENTLY`.

The container's memory parameters are deliberately small (`shared_buffers=128MB`,
`work_mem=4MB`) so that disk spills and autovacuum activity become observable at ~10M rows
instead of ~1B. Do not "fix" them. The image is `imresamu/postgis` rather than
`postgis/postgis` because the latter publishes amd64 only, and emulation on Apple Silicon
makes every `EXPLAIN ANALYZE` timing meaningless.

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

CI (`.github/workflows/`) runs `ruff check api/` + `npm run build`, and pytest with
`ORS_API_KEY`/`TOMTOM_API_KEY` secrets. Isochrone/directions tests are marked
`requires_ors` and **skip in CI** — ORS's free tier blocks datacenter IPs.

### Environment variables (`.env` at repo root)
```
ORS_API_KEY=                   # required for isochrone + directions
TOMTOM_API_KEY=                # required for traffic
DATABASE_URL=                  # optional; omit to run without Postgres (see below)
ES_URL=                        # optional; omit to disable ES and fall back to PDOK/Nominatim
UPSTASH_REDIS_REST_URL=        # optional; omit to disable caching (degrades silently)
UPSTASH_REDIS_REST_TOKEN=
ETL_CRON_TOKEN=                # bearer token guarding housing ETL/monitor endpoints
DEEPSEEK_API_KEY=              # required for housing chat / monitor / city narrative
```

## Architecture

### Request flow
```
Browser → api.js → Vite proxy → FastAPI (api/main/app.py)
                                    ↓ middleware: RequestLogging → CORS
                                    ↓ router (api/main/routers/ or domains/<d>/router.py)
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
    housing/      housing-market domain, owns its own router.py (see below)
  routers/        isochrone, pois, directions, region, traffic, search, health
                  — not yet folded into domains/ (refactor phases B & C)
```

**`upstream/` is a transport layer only.** It may not contain business rules, caching
decisions, or domain types. Judgement call: if a function in `upstream/` needs to know what a
"listing" or an "isochrone" is, it belongs in a domain instead. Organising it per upstream API
is correct *because* the business semantics now live in `domains/`.

**Every Postgres-backed path must degrade, not fail, when `DATABASE_URL` is unset.**
`db.enabled()` is the guard; the pre-Postgres code path stays in place behind it, exactly
as `cache.py` degrades when Upstash is unconfigured. This keeps the Vercel deployment
working while Postgres is local-only. Current tables: `geocode_cache`, `municipality`.

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
| `/pois` | GET | Overpass | 6h |
| `/region` | GET | PDOK reverse | 7d |
| `/directions` | GET | ORS | — |
| `/bulk_directions` | GET | ORS (`asyncio.gather`) | — |
| `/traffic/tile-url` | GET | — (static URL) | — |
| `/traffic/flow` | GET | TomTom Flow | 5m |
| `/traffic/incidents` | GET | TomTom Incidents | 5m |
| `/search` | GET | ES → PDOK → Nominatim | — |
| `/housing/providers` | GET | — | — |
| `/housing/listings` | GET | provider + PDOK geocode | `geocode_cache` table |
| `/housing/choropleth` | GET | provider/Upstash + `municipality` table | — |
| `/housing/heatmap` | GET | provider + PDOK geocode | — |
| `/housing/etl/run` | POST | provider → Upstash | — |
| `/housing/monitor/run` | POST | Upstash + DeepSeek | — |
| `/housing/chat` | POST | DeepSeek (tool calling) | — |
| `/housing/city-report` | GET | provider + DeepSeek | — |

Cache keys are `{feature}:{param}:{param}:…`, e.g. `iso:{lng}:{lat}:{minutes}:{profile}`.

Mode constants live **only** in `config.py`:
- `MODE_MAPPING`: `walking → foot-walking`, `cycling → cycling-regular`, `driving → driving-car`
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

### Housing domain (`api/main/domains/housing/`)
Provider-agnostic by design — the router only touches `models`, `registry`, and `geocoder`.

```
router.py      /housing/* endpoints; registered in app.py as `domains.housing.router`
models.py      HousingListing dataclass + HousingProvider ABC
registry.py    get() / register() / all_providers()
providers/
  h2s.py       Holland2Stay GraphQL; uses curl_cffi to pass Cloudflare TLS fingerprinting
geocoder.py    PDOK address → (lat, lng), geocode_cache table, city-centre fallback
upstash.py     Upstash REST client for listing snapshots (pipeline support)
etl.py         snapshot listings; diffs new / disappeared / booked
stats.py       per-city stats by property type + DeepSeek narrative
chat_agent.py  DeepSeek tool calling: get_city_stats, get_market_overview, respond
monitor.py     summarises listings new in the last N hours
boundaries.py  municipality polygons for choropleth (table, WFS as fallback)
```

**Geocoding has two correctness traps, both guarded — do not remove the guards.**

1. PDOK's free search is fuzzy and *always* returns its best match, so a malformed
   address resolves to an arbitrary place in the country (house number `9999` matches
   postcode `9999XJ` in Stitswerd). `_pdok_lookup` therefore rejects any result whose
   `woonplaatsnaam` is not the expected city and falls back to the city centre.
2. PDOK does not index the English/colloquial H2S city names. `_PDOK_CITY` translates
   them before lookup — without it, every **The Hague** address geocodes to **Zwolle**.

`geocode_cache.source` distinguishes `'pdok'` from `'city_centre'`. A `city_centre` row is
retried against PDOK after 7 days, and the upsert's `WHERE` clause prevents a fallback
from ever overwriting a precise coordinate.

Adding a provider: implement `HousingProvider` in `providers/`, import it from
`providers/__init__.py` (the import is what triggers self-registration).

`/housing/etl/run` and `/housing/monitor/run` require `Authorization: Bearer {ETL_CRON_TOKEN}`;
they are driven externally by QStash.

### Frontend
`MapContainer.jsx` is the single source of truth:
- `params`: `{ center: { lng, lat }, minutes, mode }` — any change triggers a full re-fetch
- `data`: `{ iso, pois, loading }`
- Re-fetch driven by `useEffect([isReady, params.center, params.minutes, params.mode])`

`useMap.js` initialises MapLibre and returns `{ map, isReady }`. All map mutations are imperative,
inside `useEffect` in child components that receive `map` as a prop. **Never mutate the map before
`isReady === true`.**

POIs are filtered to those inside the isochrone with Turf `booleanPointInPolygon` in a `useMemo` —
raw Overpass results extend past the isochrone boundary.

Components: `SearchBar`, `CenterMarker`, `PoiMarker`, `PoiPopup`, `LegendPanel`,
`AnalysisPanel` + `AnalysisResultPanel` (AI service), `HousingLayer`, `HousingChatPanel`,
`CityReportPanel`.

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

- **The Holland2Stay GraphQL endpoint now returns 403, and there is no fix.** Cloudflare
  serves a *managed challenge* (`cf-mitigated: challenge`), which requires executing
  JavaScript and returning a Turnstile token. `curl_cffi` only forges the TLS/JA3
  ClientHello and has no JS engine, so all seven impersonation profiles return a
  byte-identical 403 — the fingerprint is no longer the discriminator. `/housing/listings`,
  `/heatmap`, `/city-report`, `/chat` and `/choropleth?direction=future` all fail.
  This does **not** block Postgres stage 1 (an earlier note claiming so was wrong): stage 1
  exercises idempotent upsert, the `etl_run` state machine and the sentinel check, none of
  which depend on which provider supplies rows. Every other Dutch commercial rental source
  is behind the same protection — see `docs/data-source-feasibility.md` for the measured
  matrix and the open-government alternatives. Do not attempt challenge circumvention.
- `providers/h2s.py:232` lets `curl_cffi.requests.exceptions.HTTPError` escape instead of
  translating it to `ExternalServiceError`, so an upstream 403 surfaces as **500** rather
  than 502. This violates the error-handling standard below — providers are upstream calls.
- `api/main/cache.py:19,35` uses `print()` for cache failures instead of the structured logger.
- `domains/housing/router.py:187` docstring says the monitor uses "Claude" — it uses DeepSeek.
- `/housing/etl/run` and `/housing/monitor/run` return **422, not 401**, when the
  `Authorization` header is absent: the header is a required `Header(...)` param, so FastAPI
  rejects it as a validation error before `_verify_cron_token` ever runs.
- `AnalysisPanel.jsx:35` sends mode `"driving-car"`, but `MODE_MAPPING`/`SPEED_MAP` are keyed on
  `"driving"` — it silently falls back to foot-walking at 80 m/min. **Driving mode is broken.**
- `MapContainer.jsx:46` is `const [, setHoveredRoute] = useState(null)` — the value is discarded,
  so every POI hover spends an ORS `/directions` call that is never rendered.
- `/traffic/flow` has an `api.js` method but no caller; `/bulk_directions` has no frontend method.
- Traffic data in the AI service is a PV27 (North Holland) provincial proxy applied to **every**
  municipality; results for other provinces are not location-specific.
- `ruff check tests/` reports 3 pre-existing violations (two unused `pytest` imports in
  `tests/integration/`, one `E741` in `test_search_logic.py`). CI only lints `api/`, so they
  are invisible there.
- No test coverage for the housing subsystem, the DeepSeek agents, cron-token validation, or any
  error path (502/504 handling). `tests/unit/test_geocode_key.py` covers only the geocode
  cache-key normalisation.
- No rate limiting; upstream quotas are the only backpressure.
- Only one housing provider (`h2s`) is implemented.
