# Running MapScale on another machine

Everything in containers. The target needs **Docker** (with the `compose` plugin) and
nothing else — no Python, no Node, no Postgres client.

All figures below were measured on a cold run of this exact bundle, not estimated.

| | |
|---|---|
| Disk, base stack | **~1.6 GB** — images 914 MB (postgis 643, api 219, web 52), restored database 335 MB, bundle 34 MB |
| Disk, with routing | **+1.5 GB** — ORS image 492 MB, graphs + elevation cache ~1 GB |
| Disk, with monitoring | **+850 MB** — Grafana 539 MB, Prometheus 289 MB, exporter 21 MB |
| RAM | ~1.5 GB base. **+4 GB** while self-hosted routing builds its graphs. |
| First `up --build` | **47 s of build steps** (npm ci + pip install dominate), plus pulling ~1.4 GB of base images — so wall-clock on a fresh machine is network-bound, not CPU-bound |
| Cold start, empty volume | **37 s**, of which 22 s is restoring the dump |
| Every start after that | **16 s** |

Verified by extracting this bundle into an empty directory, `cp deploy/env.example .env`, and `up -d --build`: 54 s to all-three-healthy, then 518 POI and 584 building permits served through nginx.

## Quick start

```bash
tar xzf mapscale-bundle-<date>.tar.gz
cd mapscale-<date>

cp deploy/env.example .env          # then edit: see "Keys" below
docker compose -f docker-compose.deploy.yml up -d --build
```

Then open **http://localhost:8088**. Change `WEB_PORT` in `.env` if that port is taken.

Check it came up for real rather than just started:

```bash
docker compose -f docker-compose.deploy.yml ps          # all three must say (healthy)
curl -s localhost:8000/api/main/health

# Central Amsterdam, NOT the app's default centre — see "Two things that look broken"
# below. This must print a number in the hundreds; 0 means the database is empty.
curl -s "localhost:8000/api/main/pois?lng=4.892&lat=52.373&minutes=10&profile=walking&categories=cafe&categories=restaurant" \
  | python3 -c "import json,sys; print(len(json.load(sys.stdin)['elements']), 'POI')"
```

## What is in the bundle

```
api/            FastAPI backend (the thing the map talks to)
frontend/       React + Vite + MapLibre source; built inside the web image
migrations/     numbered .sql, applied on every api boot (forward-only, idempotent)
data/
  mapscale.dump       35 MB pg_dump: 512,861 POI + 61,853 announcements + 342 municipalities
  *.osm.pbf           only with --with-extract; needed solely for self-hosted routing
deploy/         the Dockerfiles, nginx config, env template and this file
docs/           the measurement notes (pg-notes/ is the interesting part)
docker-compose.deploy.yml
```

The database is **restored from the dump on first start** and only then — the postgres
image runs `deploy/initdb/` once, on an empty volume. `deploy/initdb/20-restore.sh`
prints the row counts afterwards and fails the boot if they are zero, because a restore
that silently produced an empty database looks exactly like a working one until the first
map load.

To watch it happen:

```bash
docker compose -f docker-compose.deploy.yml logs postgres | grep 20-restore
```

To start over with a clean database:

```bash
docker compose -f docker-compose.deploy.yml down -v    # -v deletes the volumes
```

## Keys

Every one of them is optional, and every one degrades a different feature. Nothing here
stops the stack from booting.

| Variable | Without it |
|---|---|
| `ORS_API_KEY` | isochrones fail, `/pois` falls back to a geodesic circle — the catchment shown is wrong, not missing |
| `TOMTOM_API_KEY` | the Traffic toggle errors; the rest of the map is unaffected |
| `ES_URL` | `/search` skips Elasticsearch and degrades to PDOK → Nominatim. **This is the normal state** — the ES index was never populated |
| `DEEPSEEK_API_KEY` | not needed: the AI panel goes through nginx to a separate Vercel deployment |

## Routing: three choices

`/isochrone` is the one upstream the project cannot do without, and the public service
allows **50 requests per day per endpoint**. `/pois` and `/bekendmakingen` each spend one
isochrone server-side, so that budget goes in about twenty clicks.

**1. Public API** (default). Free key from `openrouteservice.org/dev/#/signup`. Fine for a
walk-through, not for a demo that lasts an hour.

**2. Self-hosted, in this stack.** No key, no quota, and `foot-walking` works here while
the public deployment black-holes it.

```bash
# .env:  ORS_BASE_URL=http://ors:8082/ors/v2
docker compose -f docker-compose.deploy.yml up -d ors      # naming it activates its profile
docker compose -f docker-compose.deploy.yml up -d api       # recreate so it picks up the URL
docker compose -f docker-compose.deploy.yml logs -f ors
```

It needs an `.osm.pbf` in `./data/` (`--with-extract` puts Noord-Holland there, 181 MB;
otherwise download it from `download.geofabrik.de/europe/netherlands/`). First start
builds one routing graph per profile: **211 s for three graphs** over that extract, peak
JVM heap 2.03 GB, peak container RSS 2987 MiB. The healthcheck does not go green until
all three profile names appear in `/ors/v2/status`, which is deliberate — `/ors/v2/health`
answers `ready` while zero graphs are loaded.

Do **not** point it at `netherlands-latest.osm.pbf` unless the machine has >16 GB: ORS
needs roughly 8–10× the extract size in heap, so the 1.3 GB national extract wants 10–13
GB. That was measured before trying, not after an OOM.

**3. Neither.** The map still works; the isochrone becomes a circle. The POI counts it
produces are not the same question.

> One asymmetry worth knowing: `poi` holds the whole country (512,861 rows), while the
> bundled extract is Noord-Holland only. Outside that province there are POIs but no
> isochrone, so `/pois` falls back to its circle there. In Utrecht specifically a 10-minute
> walk answers 0 permits where the truth is 597 — an open, known bug, tracked in
> `docs/ors-self-host.md`, not something the packaging introduced.

## Monitoring (optional)

```bash
docker compose -f docker-compose.deploy.yml --profile monitoring up -d
```

Grafana on **:3000** (anonymous admin, no login page), Prometheus on **:9090**. It uses
`deploy/prometheus.yml`, not `monitoring/prometheus.yml` — the latter reaches the API
through `host.docker.internal` because in the development stack the API runs on the host.

Grafana's anonymous-admin setting is fine on a laptop and **not** fine on anything with a
public IP.

## Two things that look broken and are not

**The map opens with no POI markers.** The app's default centre is `4.936, 52.338`
(`frontend/src/hooks/useMap.js`, `MapContainer.jsx`), and in a 10-minute walk from there
the database holds **1 café and 0 supermarkets** — the area is car parks, recycling points
and bicycle parking. The default category set is `supermarket, convenience, gym`, so a
fresh load legitimately renders **zero markers**.

Verified identical on the development stack, so it is not something the packaging
introduced. Click or search your way to central Amsterdam (`4.892, 52.373`) and the same
build returns 48 markers for the default categories, 528 for café + supermarket +
restaurant. Easiest check from a shell:

```bash
curl -s "localhost:8000/api/main/pois?lng=4.892&lat=52.373&minutes=10&profile=walking" \
  | python3 -c "import json,sys; print(len(json.load(sys.stdin)['elements']), 'POI')"
```

**The "AI analysis" button returns 500.** The separate `mapscale-ai-service` deployment
still has the dead Upstash Redis configured and fails closed on it:
`getaddrinfo ENOTFOUND epic-basilisk-65626.upstash.io`. Measured identical through this
nginx, through the development Vite proxy, and by calling Vercel directly with no proxy at
all — so it is that service, not the bundle. Nothing here can fix it; it lives in the
other repository.

## Troubleshooting

**The map loads but has no markers and no permits.** Check the database actually has rows:

```bash
docker compose -f docker-compose.deploy.yml exec postgres \
  psql -U mapscale -d mapscale -c "select count(*) from poi"
```

Zero means the volume was created before the dump was in place. `down -v`, confirm
`data/mapscale.dump` exists, `up -d` again.

**`api` keeps restarting.** It runs the migrations before binding the port, so read its
log rather than the health status: `logs api`. A migration failure is printed there in
full.

**Isochrones all fail; `/pois` returns a circle.** Look at the first lines of `logs api`.
If it warns that `ORS_BASE_URL` points at the container, a development `.env` leaked in —
`docker compose -f docker-compose.deploy.yml` reads `./.env` for variable substitution,
and on the machine this bundle was built from, that file points at the host's `:8080`.
Use `--env-file deploy/<something>.env` to be explicit.

**Running this next to the development stack on the same machine.** It works —
`name: mapscale-deploy`, its own volumes — but the published ports collide. Change
`WEB_PORT` / `API_PORT` / `PG_PORT` / `GRAFANA_PORT` / `PROMETHEUS_PORT` in `.env`, and
pass `--env-file` so the development `.env` is not picked up instead.

## Refreshing the data later

The bundle's dump is a snapshot. To make a new one from a machine that has the live
database running:

```bash
./scripts/package.sh                 # source + fresh 35 MB dump
./scripts/package.sh --with-extract  # also the 181 MB OSM extract
./scripts/package.sh --no-dump       # source only; target starts empty
```

Re-importing from upstream instead of restoring a dump is possible but slow and needs the
host toolchain: `scripts/import_poi.py` wants the 1.3 GB extract and ~3 minutes,
`scripts/import_bekendmaking.py` runs per municipality against a government SRU endpoint
with no national mode. That is the whole reason the dump is in the bundle.
