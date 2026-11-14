#!/usr/bin/env bash
#
# Builds one tarball that runs this whole project on another machine with nothing but
# Docker installed.
#
#   ./tools/package.sh                 # source + 35 MB database dump
#   ./tools/package.sh --with-extract  # also the 181 MB OSM extract, for self-hosted ORS
#   ./tools/package.sh --no-dump       # source only, target starts with an empty DB
#
# It ships SOURCE plus DATA, not images. Two reasons, both load-bearing:
#
#   1. `docker save` produces an archive locked to the architecture that built it. This
#      laptop is arm64; a server is usually amd64. All three base images are multi-arch,
#      so building on the target works everywhere and saving does not.
#   2. The data is the part that cannot be rebuilt on the target. The POI import needs a
#      1.3 GB extract and ~3 minutes; the bekendmaking import needs 61,853 round trips to
#      a government SRU endpoint. A 35 MB dump replaces both.
#
set -euo pipefail

cd "$(dirname "$0")/.."

PG_CONTAINER="${PG_CONTAINER:-mapscale-pg}"
WITH_EXTRACT=0
WITH_DUMP=1

for arg in "$@"; do
    case "$arg" in
        --with-extract) WITH_EXTRACT=1 ;;
        --no-dump)      WITH_DUMP=0 ;;
        -h|--help)      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

STAMP="$(date +%Y%m%d)"
OUT="dist/mapscale-bundle-${STAMP}.tar.gz"
mkdir -p dist data

# ── 1. Database dump ────────────────────────────────────────────────────────
#
# Taken through the container because pg_dump is not on this machine's PATH (neither is
# psql, nor python — everything goes through .venv/bin or docker exec here).
if [ "$WITH_DUMP" -eq 1 ]; then
    if ! docker exec "$PG_CONTAINER" true 2>/dev/null; then
        echo "package: container '$PG_CONTAINER' is not running." >&2
        echo "package: start it with 'docker compose up -d --wait postgres', or pass --no-dump" >&2
        echo "package: to ship source only (the target then comes up with an empty database)." >&2
        exit 1
    fi

    echo "==> pg_dump from $PG_CONTAINER"
    # -Fc so the target can pg_restore selectively; -Z6 because the custom format is not
    # compressed by default and this is 95% geometry.
    docker exec "$PG_CONTAINER" pg_dump -U mapscale -d mapscale -Fc -Z6 -f /tmp/mapscale.dump
    docker cp "$PG_CONTAINER:/tmp/mapscale.dump" data/mapscale.dump
    docker exec "$PG_CONTAINER" rm -f /tmp/mapscale.dump

    # A dump is not evidence until something has read it back. `pg_restore -l` parses the
    # table of contents without a server, so this catches a truncated `docker cp` here
    # rather than during initdb on the other machine, where the failure is unrepeatable.
    objects=$(docker run --rm -v "$PWD/data:/d:ro" imresamu/postgis:16-3.4 \
                  pg_restore -l /d/mapscale.dump | grep -c '^[0-9]' || true)
    echo "    data/mapscale.dump  $(du -h data/mapscale.dump | cut -f1)  ($objects objects)"
    if [ "$objects" -lt 10 ]; then
        echo "package: dump has only $objects objects — refusing to ship it." >&2
        exit 1
    fi
else
    echo "==> skipping pg_dump (--no-dump)"
fi

# ── 2. Tarball ──────────────────────────────────────────────────────────────
#
# An explicit exclude list rather than `git archive`, because deploy/ and the dump are
# the two things most likely to be uncommitted at the moment of packaging, and a bundle
# that silently omits the deployment files is worse than no bundle.
EXCLUDES=(
    --exclude=.git
    --exclude=.venv
    --exclude=node_modules
    --exclude=dist
    --exclude=__pycache__
    --exclude=.pytest_cache
    --exclude=.ruff_cache
    --exclude=.DS_Store
    --exclude=reports            # local working notes, already gitignored
    --exclude='docs/*.png'       # 40 MB of screenshots; the .md notes are the valuable part
    --exclude=.env               # real API keys. deploy/env.example is the template.
)

if [ "$WITH_EXTRACT" -eq 1 ]; then
    if ! ls data/*.osm.pbf >/dev/null 2>&1; then
        echo "package: --with-extract but data/ has no .osm.pbf" >&2
        exit 1
    fi
    # The national extract is 1.3 GB and was MEASURED as unbuildable in an 8 GB Docker VM
    # (ORS needs 8-10x the extract size in heap). Shipping it would be shipping a trap.
    EXCLUDES+=(--exclude='data/netherlands-latest.osm.pbf')
    echo "==> including OSM extract(s): $(ls data/*.osm.pbf | grep -v netherlands-latest | tr '\n' ' ')"
else
    EXCLUDES+=(--exclude='*.osm.pbf')
fi

echo "==> staging"
# Staged through a directory rather than renamed inside the archive. `tar --transform` is
# GNU-only and this machine ships bsdtar, which fails the build outright with "Option
# --transform is not supported" — and `-s` would be the mirror-image trap on a Linux
# packaging host. Copying costs a few seconds and works on both.
#
# The staging directory is what makes the archive unpack into ITS OWN folder instead of
# spraying ~200 files into whatever directory it was extracted in.
STAGE="dist/stage/mapscale-${STAMP}"
rm -rf dist/stage
mkdir -p "$STAGE"
tar cf - "${EXCLUDES[@]}" ./ | tar xf - -C "$STAGE"

echo "==> tar"
tar czf "$OUT" -C dist/stage "mapscale-${STAMP}"
rm -rf dist/stage

echo
echo "bundle: $PWD/$OUT  ($(du -h "$OUT" | cut -f1))"
echo
echo "On the other machine:"
echo "  tar xzf $(basename "$OUT") && cd mapscale-${STAMP}"
echo "  cp deploy/env.example .env        # then fill in ORS_API_KEY / TOMTOM_API_KEY"
echo "  docker compose -f docker-compose.deploy.yml up -d --build"
echo "  open http://localhost:8088        # deploy/README.md has the rest"
