# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MapScale is a geographic analysis tool for urban accessibility and POI density studies. It calculates travel-time isochrones and fetches nearby Points of Interest (supermarkets, gyms) for a given map center.

**Tech Stack:**
- Frontend: React 18 + Vite, MapLibre GL, Turf.js, Axios, Recharts
- Backend: FastAPI (Python), HTTPX (async)
- External APIs: OpenRouteService (isochrones, directions), Overpass API (POIs), Nominatim (geocoding)
- Infrastructure: PostGIS + Martin vector tile server (via Docker)

## Development Commands

### Frontend
```bash
cd frontend
npm install
npm run dev       # http://localhost:5173
npm run build
npm run preview
```

### Backend
```bash
cd backend
uvicorn main:app --reload   # http://localhost:8000
```

Requires `backend/.env`:
```
ORS_API_KEY=<your_openrouteservice_api_key>
```

### Infrastructure
```bash
docker-compose up -d    # PostGIS :5432, Martin tile server :3000
docker-compose down
```

## Architecture

### Request Flow
```
User interaction → MapContainer (state) → api.js → Vite proxy → FastAPI backend → External APIs
```

### API Proxy (`vite.config.js`)
- `/api/main/*` → `http://localhost:8000` (FastAPI)
- `/api/ai/*` → `http://localhost:3000` (AI microservice, in development)

### State Management
`MapContainer.jsx` is the single source of truth:
- `params`: `{ center: { lng, lat }, minutes, mode }` — any change triggers a full re-fetch
- `data`: `{ iso: GeoJSON | null, pois: [], loading: bool }`
- Re-fetch is driven by `useEffect([isReady, params.center, params.minutes, params.mode])`

### Map Rendering Pattern
`useMap.js` initializes MapLibre GL and returns `{ map, isReady }`. All map mutations (add/update sources, layers, markers) happen imperatively inside `useEffect` hooks in child components that receive `map` as a prop. The map must not be mutated before `isReady === true`.

### POI Filtering
`MapContainer` filters POIs to those inside the isochrone using `@turf/turf` `booleanPointInPolygon` in a `useMemo`. Raw POIs from the API may extend beyond the isochrone boundary.

### Backend Endpoints
| Endpoint | Description |
|---|---|
| `GET /api/isochrone` | ORS isochrone polygon (`lng`, `lat`, `minutes`, `profile`) |
| `GET /api/pois` | Overpass POIs with radius derived from travel speed |
| `GET /api/analysis` | Combined isochrone + POI (single round-trip) |
| `GET /api/directions` | Single route via ORS |
| `GET /api/bulk_directions` | Parallel routes via `asyncio.gather` |

Travel speed constants (m/min): walking=80, cycling=250, driving=800.

### AI Analysis Feature (branch: `feature/ai-env-analysis`)
`AnalysisPanel` → `api.analyzeArea()` → `POST /api/ai/analyze` → AI microservice (port 3000, not yet implemented). `AnalysisResultPanel.jsx` renders the response with a Recharts housing price chart.

---

## Coding Standards

### 1. Async Error Handling
Every async operation — whether a fetch, a MapLibre imperative call, or an external API request — **must** be wrapped in try/catch with meaningful error propagation. Silent failures are not acceptable.

**Frontend:** API calls in `api.js` must either handle errors internally or throw typed errors that callers can distinguish. Do not return raw Promise chains without `.catch()`.

**Backend:** Every `httpx` request must be wrapped in try/except. Catch `httpx.RequestError` (network-level) separately from non-2xx responses. Use `asyncio.gather(*tasks, return_exceptions=True)` for bulk calls so one failure doesn't abort the batch.

```python
# correct
try:
    resp = await client.post(url, ...)
    resp.raise_for_status()
    return resp.json()
except httpx.TimeoutException:
    raise HTTPException(status_code=504, detail="Upstream timeout")
except httpx.RequestError as e:
    raise HTTPException(status_code=502, detail=f"Network error: {e}")
```

### 2. Modular API Definitions with Type Annotations
`api.js` is the single contract between the frontend and all backends. Each method must document its parameters and return shape.

```js
// correct
/**
 * @param {number} lng
 * @param {number} lat
 * @param {number} minutes
 * @param {'walking'|'cycling'|'driving'} profile
 * @returns {Promise<GeoJSON.FeatureCollection>}
 */
fetchIsochrone: (lng, lat, minutes, profile) => { ... }
```

On the Python side, FastAPI endpoints must declare a `response_model` or an explicit return type annotation. Shared constants (e.g., `MODE_MAPPING`, speed tables) must not be duplicated across endpoints — define once at module level.

### 3. UI Logic/Style Decoupling
Components must not mix inline style objects with conditional logic inside JSX. Styles belong outside the component function — as module-level `const` objects or in a co-located `.css` / CSS module file. Imperative DOM styling (e.g., inside `PoiMarker`) should use predefined style objects, not ad-hoc `Object.assign` literals.

```jsx
// wrong — logic and style tangled in JSX
style={{ opacity: isLoading ? 0.6 : 1, transform: isActive ? 'scale(0.98)' : 'scale(1)' }}

// correct — logic computes a class or named variant, style stays outside
const buttonStyle = { ...baseButtonStyle, ...(isLoading && disabledOverride) };
```

### 4. `console.log` for Key Path Tracing
Critical execution paths must emit `console.log` to aid debugging. "Critical" means: incoming API requests, outgoing external calls, state transitions, and map lifecycle events.

**Backend:** log at function entry with key params, and log the result shape before returning.
```python
print(f"[isochrone] lng={lng} lat={lat} minutes={minutes} profile={profile}")
print(f"[isochrone] ORS response features={len(data.get('features', []))}")
```

**Frontend:** log param changes that trigger fetches, API response summaries, and map readiness.
```js
console.log('[MapContainer] params changed, fetching', params);
console.log('[api] fetchIsochrone response', { featureCount: data.features?.length });
```

---

## Known Issues

- `PoiMarker.jsx:77` calls `api.fetchDirections()`, which is not defined in `api.js` — this silently fails on every hover.
- `backend/main.py` `get_analysis` calls `fetch_isochrone_from_provider()` and `fetch_overpass_data()` — neither function exists, making this endpoint broken at runtime.
- `MODE_MAPPING` is defined at module level in `main.py` but `get_isochrone` duplicates it as a local `profile_map` dict — inconsistency.
- `Map.jsx` in `frontend/src/components/` is legacy and unused; `MapContainer.jsx` is the active component.
