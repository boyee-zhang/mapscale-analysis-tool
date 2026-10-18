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
- `cycling` mode is broken for users until upstream fixes its graph. There is no workaround.
- Revisit `MODE_MAPPING["walking"]` if/when `foot-walking` recovers. Nothing detects that
  automatically.
- Self-hosting ORS (docker, NL extract — we already download one for `import_poi.py`) would
  remove the dependency on a free tier with no SLA entirely, and would make `/v2/status`
  available so broken graphs are visible. Out of scope here; noted as the structural answer.
