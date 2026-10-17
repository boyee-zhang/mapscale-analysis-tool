# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MapScale is a geographic analysis tool for the Netherlands. It started as an urban accessibility
tool (travel-time isochrones + POI density) and has grown two more subsystems: a three-tier
address search, and a housing-market subsystem (rental listings, choropleth, AI reports).

**Tech Stack:**
- Frontend: React 18 + Vite 6, MapLibre GL 5, Turf.js, Axios, Recharts
- Backend: FastAPI (Python 3.12), HTTPX (async), deployed as a single Vercel serverless function via Mangum
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

### Elasticsearch (optional)
```bash
docker-compose up -d    # Elasticsearch :9200, single-node, security disabled
python scripts/ingest_addresses.py          # seed nl_addresses from PDOK
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
                                    ↓ router (api/main/routers/)
                                    ↓ cache check (Upstash)
                                    ↓ client (api/main/clients/) or housing service
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
  logger.py       JSON logging + request-ID middleware
  clients/        thin upstream wrappers: ors, overpass, pdok, tomtom, es_client
  routers/        one module per feature area
  housing/        housing-market subsystem (see below)
```

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
| `/housing/listings` | GET | provider + PDOK geocode | in-memory geocode cache |
| `/housing/choropleth` | GET | provider/Upstash + PDOK WFS | — |
| `/housing/heatmap` | GET | provider + PDOK geocode | — |
| `/housing/etl/run` | POST | provider → Upstash | — |
| `/housing/monitor/run` | POST | Upstash + DeepSeek | — |
| `/housing/chat` | POST | DeepSeek (tool calling) | — |
| `/housing/city-report` | GET | provider + DeepSeek | — |

Cache keys are `{feature}:{param}:{param}:…`, e.g. `iso:{lng}:{lat}:{minutes}:{profile}`.

Mode constants live **only** in `config.py`:
- `MODE_MAPPING`: `walking → foot-walking`, `cycling → cycling-regular`, `driving → driving-car`
- `SPEED_MAP` (m/min): `walking 80`, `cycling 250`, `driving 800` — used to derive the Overpass search radius

`clients/overpass.py` fails over across three mirrors (kumi.systems → overpass-api.de → openstreetmap.fr).

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

### Housing subsystem (`api/main/housing/`)
Provider-agnostic by design — the router only touches `models`, `registry`, and `geocoder`.

```
models.py      HousingListing dataclass + HousingProvider ABC
registry.py    get() / register() / all_providers()
providers/
  h2s.py       Holland2Stay GraphQL; uses curl_cffi to pass Cloudflare TLS fingerprinting
geocoder.py    PDOK address → (lat, lng), in-memory cache, city-centre fallback
upstash.py     Upstash REST client for listing snapshots (pipeline support)
etl.py         snapshot listings; diffs new / disappeared / booked
stats.py       per-city stats by property type + DeepSeek narrative
chat_agent.py  DeepSeek tool calling: get_city_stats, get_market_overview, respond
monitor.py     summarises listings new in the last N hours
boundaries.py  PDOK CBS WFS → municipality polygons for choropleth
```

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

**Backend:** upstream calls live in `clients/` and must translate HTTPX failures into
`ExternalServiceError` (the app-level handler maps it to 502, or 504 for timeouts).
Routers catch `ExternalServiceError`/`HTTPException` and re-raise, and wrap anything else in
`InternalError` after logging with `exc_info=True`.

```python
# clients/ — translate upstream failures
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

- `api/main/cache.py:19,35` uses `print()` for cache failures instead of the structured logger.
- `routers/housing.py:187` docstring says the monitor uses "Claude" — it uses DeepSeek.
- Traffic data in the AI service is a PV27 (North Holland) provincial proxy applied to **every**
  municipality; results for other provinces are not location-specific.
- No test coverage for the housing subsystem, the DeepSeek agents, cron-token validation, or any
  error path (502/504 handling).
- No rate limiting; upstream quotas are the only backpressure.
- Only one housing provider (`h2s`) is implemented.
