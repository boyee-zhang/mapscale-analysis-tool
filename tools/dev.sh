#!/usr/bin/env bash
#
# One command to bring the whole app up locally: Postgres, the migrations, the FastAPI
# backend and the Vite dev server. Ctrl-C stops all of it.
#
#   ./tools/dev.sh
#
# Deliberately does NOT start two of the compose services:
#
#   ors             first start builds three routing graphs (tens of minutes, several GB).
#                   Run `docker compose up -d ors` by hand — see docs/ors-self-host.md.
#   elasticsearch   /search already degrades to PDOK -> Nominatim, and this project's
#                   esdata volume is empty, so an ES on the default path costs ~1 GB of RAM
#                   and answers nothing until etl/ingest/addresses.py has run. Add it
#                   with `docker compose up -d elasticsearch`.
#
set -euo pipefail

cd "$(dirname "$0")/.."

# Checked before anything starts, because the two servers fail differently and both
# failures are quiet: uvicorn exits with "[Errno 48] Address already in use" while this
# script keeps waiting, and vite silently moves to :5174, so you end up with a frontend
# talking through its proxy to somebody else's backend. Measured — a uvicorn and a vite
# left over from the morning turned a green smoke test into a test of the wrong processes.
for port in 8000 5173; do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "port $port is already taken:" >&2
        lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2
        echo "stop it first, or this script would start a half-broken stack." >&2
        exit 1
    fi
done

# --wait blocks on the healthcheck rather than on "container created", so the migration
# below cannot race the first `pg_isready`.
echo "==> postgres"
docker compose up -d --wait postgres

# Forward-only and idempotent, so this is safe on every start. It is not optional: the
# pgdata volume can outlive a migration being added.
echo "==> migrations"
.venv/bin/python tools/migrate.py

echo "==> backend  http://localhost:8000/api/main/health"
# --reload-dir api is not tidiness, it is the difference between starting and not starting.
# A bare --reload makes StatReload walk the WHOLE repo on every poll, and this repo's tree
# includes a gitignored data/ holding a 1.3 GB netherlands-latest.osm.pbf, a 181 MB province
# extract and a 185 MB embedding cache -- 1.7 GB of files the API never reads. MEASURED
# 2026-11-10 on a 16 GB machine with Docker holding 7.65 GB: the reloader parent sat at
# 36-86% CPU doing nothing but stat(), the worker it spawned spent 10+ minutes inside a
# single import at 0.0% CPU, and :8000 never bound at all while :5173 came up fine -- which
# reads from the browser as "the backend is broken" rather than as "the watcher is eating
# the disk". Only api/ is imported by the server, so only api/ needs watching.
.venv/bin/python -m uvicorn api.main.app:app --reload --reload-dir api &
backend=$!

echo "==> frontend http://localhost:5173"
# --strictPort so a busy port is an error rather than a silent move to :5174.
npm --prefix frontend run dev -- --strictPort &
frontend=$!

# Without this, Ctrl-C kills this script but leaves uvicorn and vite holding :8000 and
# :5173, and the next run fails on the bound port instead of on anything informative.
trap 'kill $backend $frontend 2>/dev/null' EXIT INT TERM
wait
