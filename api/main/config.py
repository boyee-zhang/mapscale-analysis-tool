import os
from dotenv import load_dotenv

load_dotenv()  # loads .env from project root

ORS_API_KEY = os.getenv("ORS_API_KEY")

# Everything up to and including /v2, because the three deployments disagree on the prefix:
#   https://api.openrouteservice.org/v2          legacy public host, undocumented, the default
#   https://api.heigit.org/openrouteservice/v2   what the official OpenAPI spec declares
#   http://localhost:8080/ors/v2                 self-hosted container (see docker-compose.yml)
# The default keeps Vercel unchanged. A self-hosted instance ignores the API key entirely, so
# the Authorization header stays unconditional — it is simply not read.
ORS_BASE_URL = os.getenv("ORS_BASE_URL", "https://api.openrouteservice.org/v2").rstrip("/")
ES_URL = os.getenv("ES_URL")  # optional — omit to disable ES and fall back to PDOK/Nominatim
TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY")
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")  # optional — omit to run without Postgres

# ORS profile outage, measured 2026-10-18. Full evidence: docs/pg-notes/ors-profile-outage.md.
# `foot-walking` and all four `cycling-*` profiles are still listed in the official OpenAPI
# spec (docs.openrouteservice.org/openrouteservice/v2/docs), so neither is deprecated and our
# request bodies validate against it — the server-side graphs are simply broken, and the two
# break in opposite ways.
MODE_MAPPING = {
    # NOT foot-walking. That profile black-holes the request: auth succeeds (a bogus key
    # still 403s in 94 ms), the body uploads, then nothing comes back — on /isochrones,
    # /directions and /matrix alike, at range=60 as well as range=600, on both the legacy
    # api.openrouteservice.org host and the spec's api.heigit.org host. Our 15 s timeout
    # turned that into a 504 and made walking, the default mode, unusable.
    # foot-hiking is a measured substitute, not a guess: 1.489 km2 / reachfactor 1.0 for a
    # 600 s isochrone, against wheelchair's 1.454 km2 — the same pedestrian speed model.
    "walking": "foot-hiking",
    # BROKEN UPSTREAM, and deliberately NOT re-pointed, because there is no healthy cycling
    # graph to point at: cycling-regular, -road, -mountain and -electric all return the same
    # degenerate 0.031 km2 blob for a 600 s isochrone (reachfactor 0.0016 against foot's 1.0;
    # a bike covers ~9x a walk, so this is ~400x too small), and /matrix returns null
    # durations between points 800 m apart. The danger is that it answers **HTTP 200**, so
    # neither our error handling nor the /pois circle fallback can see it — cycling silently
    # under-reports POI rather than failing. Needs an explicit plausibility check, not a
    # remap; see the Known Issues entry.
    "cycling": "cycling-regular",
    "driving": "driving-car",
}

SPEED_MAP = {
    "walking": 80,
    "cycling": 250,
    "driving": 800,
}
