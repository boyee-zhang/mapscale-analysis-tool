#!/usr/bin/env sh
#
# Migrate, then serve. Two things make running the migration on every boot safe rather
# than reckless:
#   - the runner is forward-only and records applied versions in schema_migrations, so a
#     second run applies nothing;
#   - the pgdata volume outlives the image, so a container started after a migration was
#     added would otherwise serve against an older schema.
# It is NOT safe with more than one replica of this service — two boots would race on the
# same version. Keep this service at one replica, or move the migration to its own job.
set -e

if [ -n "${DATABASE_URL:-}" ]; then
    echo "==> migrations"
    python tools/migrate.py
else
    # Not a failure: db.enabled() guards every DB path and the app degrades to Overpass
    # for POI and to no-op for /bekendmakingen. Say so loudly, because the symptom
    # (an empty map) looks identical to a broken query.
    echo "==> DATABASE_URL unset — starting WITHOUT Postgres (POI falls back to Overpass," >&2
    echo "    /bekendmakingen returns nothing). This is degraded, not broken." >&2
fi

# Inside a container, "localhost" is this container. A URL like that is almost always a
# development .env that leaked in through compose variable substitution: `docker compose
# -f docker-compose.deploy.yml` reads ./.env for substitution, and on the machine this was
# built on that file points ORS_BASE_URL at the host's :8080. The result is an isochrone
# that fails on every request while /pois quietly falls back to a geodesic circle, which
# looks like a working map with a wrong answer. Warn at boot, where it is still cheap to
# notice.
for var in ORS_BASE_URL ES_URL; do
    eval "value=\${$var:-}"
    case "$value" in
        *localhost*|*127.0.0.1*)
            echo "==> WARNING: $var=$value points at this CONTAINER, not the host." >&2
            echo "    Use a compose service name (e.g. http://ors:8082/ors/v2), or pass" >&2
            echo "    --env-file with a deployment env file. See deploy/README.md." >&2
            ;;
    esac
done

echo "==> uvicorn on :8000"
# One worker on purpose: DB_POOL_MAX_SIZE is per process, so N workers open N x pool
# connections against a server whose max_connections is the default 100. Raise both
# together or neither.
exec python -m uvicorn api.main.app:app --host 0.0.0.0 --port 8000 --workers 1
