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
same `data/noord-holland-latest.osm.pbf` that `scripts/import_poi.py` reads. Runbook and the
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
