-- Offline OSM POI, replacing the per-request Overpass call in upstream/overpass.py.
-- See docs/postgres-refactor-plan.md "阶段 1 Spec" for the full rationale.

-- Required by poi_cat_geom_gist below, which mixes a scalar column into a GiST index.
CREATE EXTENSION IF NOT EXISTS btree_gist;


-- One row per import attempt. Keyed on a free-text `source` rather than a provider FK
-- so that the POI import, a future BAG import and the (currently unreachable) housing
-- providers all share one state machine.
CREATE TABLE etl_run (
    id          bigserial PRIMARY KEY,
    source      text NOT NULL,                        -- 'osm_poi' | 'housing:h2s' | ...
    status      text NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    row_count   int,
    note        text,

    -- finished_at and status must agree, so that the partial index below really does
    -- mean "still running".
    CONSTRAINT etl_run_finished_iff_terminal
        CHECK ((status = 'running') = (finished_at IS NULL))
);

-- Stops two imports of the same source from running at once (duplicate webhook
-- delivery, or a human running the script twice).
--
-- The predicate is on finished_at rather than the more natural
-- `WHERE status = 'running'` because an UPDATE to any indexed column — including a
-- column that appears only in a partial index predicate — makes a HOT update
-- impossible, and status is the highest-churn column here.
--
-- Measured, because the obvious version of that argument is wrong: it does NOT make
-- the running->succeeded UPDATE HOT. That UPDATE sets finished_at, so it writes the
-- predicate column itself and the index has to be touched either way. See
-- docs/pg-notes/stage1-poi-import.md: 3 updates of note/row_count were HOT 3/3, and
-- the terminal update was HOT 0/1.
--
-- What the choice actually buys is that every update which is *not* the terminal one
-- stays HOT, and that an intermediate status (running -> retrying) could be added
-- later without that becoming an index write. Today etl_run_finished_iff_terminal
-- rules such a status out, so the two predicates are equivalent in practice.
CREATE UNIQUE INDEX etl_run_one_active ON etl_run (source) WHERE finished_at IS NULL;

CREATE INDEX etl_run_source_started ON etl_run (source, started_at DESC);


-- The stable category vocabulary the API exposes. Deliberately a table and not an
-- enum: adding a category must not require a migration.
CREATE TABLE poi_category (
    category text PRIMARY KEY,
    label_en text NOT NULL
);

-- OSM tag -> category. This table is the point of the whole stage: today the mapping
-- lives inside an f-string in upstream/overpass.py, where a single concept is spread
-- over unrelated keys — a gym is `leisure=fitness_centre` on some objects and
-- `amenity=gym` on others — and there is no way to query or test it.
CREATE TABLE poi_category_rule (
    osm_key   text NOT NULL,
    osm_value text NOT NULL,
    category  text NOT NULL REFERENCES poi_category (category),
    -- An object can carry several matching tags. The lowest priority wins, so that
    -- e.g. shop=supermarket beats a generic building tag deterministically.
    priority  smallint NOT NULL DEFAULT 100,
    PRIMARY KEY (osm_key, osm_value)
);

CREATE INDEX poi_category_rule_category ON poi_category_rule (category);


CREATE TABLE poi (
    -- (osm_type, osm_id) is a stable natural key upstream, so it is also the
    -- idempotency key for re-imports. No surrogate key: it would only add an index
    -- and a UNIQUE constraint to maintain.
    osm_type       char(1) NOT NULL CHECK (osm_type IN ('n', 'w', 'r')),
    osm_id         bigint NOT NULL,
    category       text NOT NULL REFERENCES poi_category (category),
    name           text,
    -- Always a point. Ways and relations (a supermarket drawn as a building outline)
    -- are reduced with ST_PointOnSurface, not ST_Centroid — the centroid of a C- or
    -- ring-shaped polygon can fall outside the polygon itself.
    geom           geometry(Point, 4326) NOT NULL,
    tags           jsonb NOT NULL DEFAULT '{}',
    first_seen_run bigint NOT NULL REFERENCES etl_run (id),
    -- "last run that changed this row", not "last run that saw it". A refresh must not
    -- touch rows whose content is unchanged: the first version of the import bumped a
    -- last_seen_run on every row every time, and one no-op re-import of a single
    -- province doubled the heap (22 -> 44 MB) and the spatial index (3.4 -> 6.8 MB)
    -- with n_tup_hot_upd = 0/82259. Disappearance is detected by anti-joining the
    -- staging table instead, which needs no write at all on unchanged rows.
    last_changed_run bigint NOT NULL REFERENCES etl_run (id),
    -- Soft delete. A refresh must never TRUNCATE-and-reload: that would make /pois
    -- return nothing for the duration of the import.
    deleted_at     timestamptz,
    PRIMARY KEY (osm_type, osm_id)
);

-- The single spatial read index, and the reason 0003 creates btree_gist. Every /pois
-- query is "these categories, inside this polygon", so category belongs *in* the
-- spatial index rather than in a sibling btree the planner has to combine.
--
-- Measured on Noord-Holland (82k rows, 1.2 km circle around Amsterdam Centraal),
-- buffers hit, versus a plain GIST (geom) plus a btree (category):
--   category = 'supermarket'  (1% of rows):  512 -> 29
--   category = 'car_parking' (40% of rows):  819 -> 65
--   category IN (3 values):                        81, one index scan
-- The gain is not the recheck, it is that the index itself returns 25 candidates
-- instead of 802 or 2817.
--
-- It also replaces GIST (geom): with both present the planner chose this one even for
-- a predicate on geom alone, and dropping the other produced a byte-identical plan.
-- Re-measure at country scale before trusting that — a geom-only search here has to
-- descend through every category subtree, and 82k rows is not where that hurts.
--
-- Partial on the live subset because every query filters out soft-deleted rows.
CREATE INDEX poi_cat_geom_gist ON poi USING GIST (category, geom) WHERE deleted_at IS NULL;

-- For arbitrary tag predicates (brand, wheelchair, cuisine) without a schema change.
-- jsonb_path_ops is half the size of the default and supports @>, which is the only
-- containment operator the API needs; it cannot answer `tags ? 'key'`, so existence
-- checks must be written as @> '{"key": ...}' or the opclass revisited.
CREATE INDEX poi_tags_gin ON poi USING GIN (tags jsonb_path_ops);

-- Deliberately NO btree on category alone. It would serve a category-only aggregate
-- ("how many pharmacies do we have?") in 4 buffers where the composite GiST needs 65 —
-- both as an Index Only Scan — but no such query exists yet. Add it with that query,
-- not before.
--
-- Deliberately NO index on last_changed_run. Detecting disappearance is an anti-join
-- against poi_import, not a scan for a stale run id.


-- Staging table for the import. Holds the upstream bytes as they arrive and nothing
-- else: pyosmium emits plain hex WKB, which carries no SRID, and a geometry column
-- would therefore have to either lie (SRID 0) or reject the COPY. Interpretation —
-- ST_SetSRID, reduction to Point, category assignment — happens on the
-- INSERT ... SELECT into poi, where it is visible as SQL.
--
-- UNLOGGED because the contents are disposable and skipping WAL roughly halves the
-- COPY time. No indexes and no constraints for the same reason.
CREATE UNLOGGED TABLE poi_import (
    osm_type char(1) NOT NULL,
    osm_id   bigint NOT NULL,
    -- Point for nodes, LineString for ways (open or closed).
    geom_wkb text NOT NULL,
    tags     jsonb NOT NULL
);
