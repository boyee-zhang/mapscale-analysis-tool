CREATE EXTENSION IF NOT EXISTS postgis;

-- Replaces the in-process dict in domains/housing/geocoder.py, which was lost on
-- every cold start. Keyed on a deterministic normalisation of the address.
CREATE TABLE geocode_cache (
    address_norm text PRIMARY KEY,
    geom         geometry(Point, 4326) NOT NULL,
    source       text NOT NULL,              -- 'pdok' | 'city_centre'
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- No spatial index here on purpose: this table is only ever point-queried by
-- primary key. Adding GiST would cost write throughput for zero read benefit.

-- Replaces the per-request PDOK CBS WFS call in domains/housing/boundaries.py.
CREATE TABLE municipality (
    code       text PRIMARY KEY,             -- CBS gemeentecode, e.g. 'GM0363'
    name       text NOT NULL,                -- CBS statnaam, e.g. "'s-Gravenhage"
    geom       geometry(MultiPolygon, 4326) NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now()
);

-- For the ST_Contains spatial joins introduced in stages 1 and 4.
CREATE INDEX municipality_geom_gist ON municipality USING GIST (geom);
CREATE INDEX municipality_name_lower ON municipality (lower(name));
