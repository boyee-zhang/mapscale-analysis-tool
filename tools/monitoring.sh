#!/usr/bin/env bash
#
# Start, stop or inspect the local monitoring stack: Prometheus, Grafana and the Postgres
# exporter. They are already defined in docker-compose.yml behind `profiles: ["monitoring"]`;
# this script is only a safe front door to them.
#
#   ./tools/monitoring.sh up       Prometheus :9090, Grafana :3000 (no login)
#   ./tools/monitoring.sh down     stops ONLY these three
#   ./tools/monitoring.sh status   container state + whether each target is really scraped
#
# Three reasons this is a script and not a line in the README:
#
#  1. `docker compose down` is PROJECT-scoped, not profile-scoped. Typed to "stop
#     monitoring" it also removes mapscale-pg and mapscale-ors — and the ORS graphs cost
#     ~40 minutes to rebuild. `down` here names the three services explicitly, which is
#     the only form that cannot take the database with it.
#  2. All three carry `profiles: ["monitoring"]`, so a bare `docker compose up -d` skips
#     them and gives no hint that it did. Naming them is what activates the profile — the
#     `--profile` flag is NOT needed for that and this script does not pass it.
#  3. A running container proves nothing about scraping. The backend runs on the HOST
#     under uvicorn, not in a container, so when it is down Prometheus is perfectly
#     healthy while the mapscale-api target is not scraped and every API panel on the
#     dashboard is empty. So `up` and `status` both end by reading /api/v1/targets and
#     reporting which jobs actually answer — the container list is not the answer.
set -euo pipefail

cd "$(dirname "$0")/.."

# Order matters on up: the exporter waits on postgres' healthcheck via depends_on, and
# grafana waits on prometheus. Compose resolves that itself; the list is just the scope.
SERVICES="prometheus grafana postgres-exporter"

targets() {
    # The wait lives inside python because two situations have to be told apart and a grep
    # cannot: an EMPTY target list and a list where everything is healthy both contain no
    # "unknown". A grep-based wait therefore exits instantly on a fresh container and
    # prints nothing at all — measured, that is exactly what the first two versions of
    # this function did. The condition is "targets exist AND none is unknown", not
    # "no unknown". Same shape as this project's rule that an empty 200 and a real answer
    # must not share a branch. /-/ready is not polled separately either: it turns true
    # BEFORE the targets are registered, so it answers a question nobody asked.
    python3 - <<'PY'
import json, sys, time, urllib.request

URL = "http://localhost:9090/api/v1/targets?state=any"
deadline = time.time() + 40
rows = []
while time.time() < deadline:
    try:
        with urllib.request.urlopen(URL, timeout=2) as resp:
            rows = json.load(resp)["data"]["activeTargets"]
    except Exception:
        rows = []
    if rows and not any(t["health"] == "unknown" for t in rows):
        break
    time.sleep(1)

if not rows:
    print("  prometheus on :9090 never reported a target", file=sys.stderr)
    sys.exit(1)

for t in sorted(rows, key=lambda x: x["labels"]["job"]):
    mark = {"up": "ok", "down": "DOWN", "unknown": "..."}.get(t["health"], t["health"])
    print("  %-5s %-14s %s" % (mark, t["labels"]["job"], t["scrapeUrl"]))
    if t.get("lastError"):
        print("          last error: " + t["lastError"])
PY
}

case "${1:-}" in
    up)
        echo "==> starting $SERVICES"
        docker compose up -d $SERVICES
        echo
        echo "==> scrape targets"
        targets || true
        echo
        echo "  Grafana    http://localhost:3000   (anonymous admin, no login form)"
        echo "  Prometheus http://localhost:9090"
        echo
        echo "  A DOWN mapscale-api means the backend is not running on the host."
        echo "  Start it with ./tools/dev.sh — nothing in this stack can do it for you."
        ;;
    down)
        # Never a bare `docker compose down`. See reason 1 in the header.
        echo "==> stopping $SERVICES (postgres and ors are left alone)"
        docker compose stop $SERVICES
        docker compose rm -f $SERVICES
        echo
        echo "  Volumes promdata and grafanadata are kept, so 7 days of samples and"
        echo "  Grafana's own state survive. The dashboard itself is provisioned from"
        echo "  monitoring/grafana/dashboards/, so it is in git either way."
        ;;
    status)
        docker compose ps $SERVICES
        echo
        echo "==> scrape targets"
        targets || true
        ;;
    *)
        sed -n '6,9p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
