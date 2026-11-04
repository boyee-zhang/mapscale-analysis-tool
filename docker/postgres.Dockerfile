# The development database: PostGIS, plus pgvector for semantic search over publications.
#
# WHY A BUILD AND NOT A PLAIN IMAGE. The base must stay `imresamu/postgis`, which is a
# deliberate choice recorded in CLAUDE.md: the official `postgis/postgis` publishes amd64
# only, and emulating it on Apple Silicon makes every EXPLAIN ANALYZE timing meaningless.
# No multi-arch image ships PostGIS and pgvector together, so the one extension we need on
# top is added here rather than by giving up the arch-native base.
#
# Installed from PGDG (already configured in the base image) rather than compiled: the
# package is prebuilt for arm64, so this adds no build toolchain and no compile step.
#
# Changing the image does NOT reinitialise the data directory. The volume records the glibc
# collation version from initdb, and that is a property of the base image and architecture,
# neither of which changes here -- only an extra extension .so and control file are added.
FROM imresamu/postgis:16-3.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
