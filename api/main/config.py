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

# ORS profile faults, measured 2026-10-18 and RE-MEASURED against a self-hosted instance
# 2026-10-28. Full evidence: docs/pg-notes/ors-profile-outage.md.
# `foot-walking` and all four `cycling-*` profiles are still listed in the official OpenAPI
# spec (docs.openrouteservice.org/openrouteservice/v2/docs), so neither is deprecated and our
# request bodies validate against it. The 2026-10-18 reading of "both server-side graphs are
# broken" did not survive the re-measurement: building the graphs ourselves from the same
# extract separates the two faults, and only one of them is in a graph at all.
MODE_MAPPING = {
    # NOT foot-walking. On the PUBLIC API that profile black-holes the request: auth succeeds
    # (a bogus key still 403s in 94 ms), the body uploads, then nothing comes back — on
    # /isochrones, /directions and /matrix alike, at range=60 as well as range=600, on both the
    # legacy api.openrouteservice.org host and the spec's api.heigit.org host. Our 15 s timeout
    # turned that into a 504 and made walking, the default mode, unusable.
    # foot-hiking is a measured substitute, not a guess: 1.489 km2 / reachfactor 1.0 for a
    # 600 s isochrone, against wheelchair's 1.454 km2 — the same pedestrian speed model.
    # 2026-10-28: a self-hosted v10.1.0 built from noord-holland-latest.osm.pbf serves
    # foot-walking perfectly — 1.489 km2 / reachfactor 1.0, byte-identical to foot-hiking, and
    # it routes 731 m in 526 s. So the black hole is in the public DEPLOYMENT, not in ORS and
    # not in a graph. This stays foot-hiking anyway: it is one global constant and production
    # on Vercel still talks to the public API, where foot-walking is still a black hole.
    # Re-pointing it would fix localhost by breaking prod.
    "walking": "foot-hiking",
    # The degenerate 0.031 km2 blob is REAL but it is NOT a broken graph, and that correction
    # cost nothing but a second coordinate. Re-measured 2026-10-28 on a self-hosted instance:
    #
    #   (4.9003, 52.3791)  0.0308 km2  rf 0.0016   <- the 2026-10-18 test point
    #   200 m east         15.124 km2  rf 0.7703
    #   200 m west         14.693 km2  rf 0.7483
    #   200 m north        14.465 km2  rf 0.7367
    #   Haarlem            14.867 km2  rf 0.7572
    #   Alkmaar            16.082 km2  rf 0.8190
    #
    # 10-16 km2 is the healthy figure the 2026-10-18 note itself predicted (~9x a 10-minute
    # walk). The blob is a property of ONE coordinate, which snaps to a bike-disconnected
    # component — /directions from it fails with ORS code 2009 while foot-walking covers the
    # same 731 m. The original "all four cycling-* graphs are broken" came from testing every
    # profile at a single point, so it measured that point, not the graphs.
    # Still NOT re-pointed, and the plausibility check is still owed: a 200 carrying a
    # 400x-too-small polygon is invisible to our error handling and to the /pois circle
    # fallback either way, so cycling silently under-reports POI at any such coordinate.
    # NOT re-verified on the public API — its 50/day quota was exhausted that day
    # ({"error":"Quota exceeded"}), so whether the public graph behaves the same away from
    # that point is UNTESTED, not confirmed.
    "cycling": "cycling-regular",
    "driving": "driving-car",
}

SPEED_MAP = {
    "walking": 80,
    "cycling": 250,
    "driving": 800,
}
