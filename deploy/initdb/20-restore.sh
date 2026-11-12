#!/usr/bin/env bash
#
# Restores the bundled pg_dump into a FRESH volume. The postgres image runs
# /docker-entrypoint-initdb.d/* exactly once — on an empty data directory — so this is a
# no-op on every later start and cannot overwrite a database that already has rows.
#
# Named 20- so it runs after the image's own 10_postgis.sh, which installs postgis.
#
# Without a dump the stack still comes up: api-entrypoint.sh runs the migrations, the
# schema exists, and every table is empty. That is a working app with an empty map.
set -euo pipefail

DUMP=/dump/mapscale.dump

if [ ! -f "$DUMP" ]; then
    echo "20-restore: no $DUMP — starting with an EMPTY database."
    echo "20-restore: migrations will create the schema; poi and bekendmaking will have 0 rows."
    exit 0
fi

echo "20-restore: restoring $(du -h "$DUMP" | cut -f1) into $POSTGRES_DB"

# --no-owner/--no-privileges so the dump's role names do not have to exist here.
# Not --exit-on-error: the dump carries CREATE EXTENSION for extensions 10_postgis.sh has
# already installed, and those are warnings, not failures. The exit code is therefore not
# the thing to trust — the row counts below are.
pg_restore \
    --no-owner --no-privileges \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    "$DUMP" || echo "20-restore: pg_restore reported warnings (see above); checking the data instead"

# The point of this check: an empty 200 and a real answer must not share a branch. A
# restore that silently produced zero rows would otherwise look exactly like a successful
# one until the first map load, and by then the initdb phase is over and unrepeatable.
read -r poi bek mun <<<"$(psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -At -F' ' -c \
    "SELECT (SELECT count(*) FROM poi), (SELECT count(*) FROM bekendmaking), (SELECT count(*) FROM municipality);")"

echo "20-restore: poi=$poi bekendmaking=$bek municipality=$mun"

if [ "$poi" -eq 0 ] || [ "$mun" -eq 0 ]; then
    echo "20-restore: FAILED — the dump restored no rows. Refusing to leave a silently empty DB." >&2
    exit 1
fi

# Mirrors migrations/0002: the image installs a US TIGER geocoder and a topology schema
# (~35 tables) that nothing here uses, and they bury the handful of real tables in
# pg_stat_user_tables. The dump does not drop them because they were already gone when it
# was taken.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
DROP EXTENSION IF EXISTS postgis_tiger_geocoder CASCADE;
DROP EXTENSION IF EXISTS postgis_topology CASCADE;
SQL

# The dump restores rows but not planner statistics, and the first query after a restore
# would otherwise plan against defaults — the project already measured that a cold read is
# ~3x slow and must be discarded. ANALYZE makes the first real request representative.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "ANALYZE;"

echo "20-restore: done"
