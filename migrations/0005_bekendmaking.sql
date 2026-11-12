-- Official Dutch government announcements (officiële bekendmakingen), from the KOOP SRU
-- service at https://repository.overheid.nl/sru. CC0.
--
-- What this adds that `poi` cannot answer: `poi` says what *is* at a place, this says what
-- *happens* there — building and event permits, traffic decisions, demolitions, tree felling
-- — with a date, so the same isochrone can be asked "what happened here, by month, by type".
--
-- Why Postgres and not a document store, given the records are semi-structured XML: the
-- load-bearing query is "announcements inside the ORS isochrone polygon", and the polygon and
-- the POI both already live here. SRU itself can only filter by a RADIUS
-- (`w.locatiepunt within/rijksdriehoek "X Y km"`, measured — there is no polygon relation and
-- no /wgs84 modifier), so the clip to the real isochrone has to happen in PostGIS either way.
-- A document store would turn that into an application-level join. The variable fields are
-- handled the way `poi.tags` already handles them, with jsonb.
--
-- Measured against the live service 2026-10-28:
--   7,404,221 records in total, 4,218,805 (57%) carrying coordinates.
--   Geo-coverage is a property of the TYPE, and it splits exactly where you would want it to:
--     andere vergunning    96%    faillissement/schuldsanering   0%
--     exploitatievergunning 98%   deurwaardersexploot            0%
--     verkeersbesluit       88%   Kamerstuk                      0%
--     omgevingsvergunning   78%   beleidsregel                   3%
--     evenementenvergunning 70%   verordening (algemeen)         3%
--   Things bound to a person, a company or the whole country have no point; things bound to
--   an address do. Geo-coded volume starts in 2013 and runs 400-500k/year since 2021;
--   Amsterdam alone is ~5,000 publications/month.


-- One row per publication. `identifier` ('gmb-2021-224076') is the publisher's own key and is
-- stable across re-fetches, so it is the primary key and the idempotency key in one: a
-- re-import of an overlapping date range is an upsert, not a duplicate. That is the only
-- protection against double-insertion that does not depend on the isolation level — two
-- concurrent workers that both check-then-insert are a write-skew-shaped bug at every level
-- below SERIALIZABLE. See docs/pg-notes/write-skew-vs-idempotency.md.
CREATE TABLE bekendmaking (
    identifier       text PRIMARY KEY,
    title            text NOT NULL,
    doc_type         text NOT NULL,              -- dt.type, e.g. 'omgevingsvergunning'
    published_at     date NOT NULL,              -- dt.date
    authority        text,                       -- dt.creator, the publishing body
    organisatietype  text,                       -- 'gemeente' | 'waterschap' | 'provincie' | ...
    publicatienaam   text,                       -- 'Gemeenteblad' | 'Staatscourant' | ...
    gemeente         text,                       -- w.ligtInGemeente; also multi-valued upstream,
                                                 -- flattened to the first because a publication
                                                 -- spanning two municipalities is rare and the
                                                 -- locations carry the real geography.
    -- w.activiteit: a small, complete vocabulary (9 values, scanned in full, unlike dt.type
    -- whose 100+ values the SRU `scan` operation cannot enumerate — it caps at 100 terms from
    -- the index start and `responsePosition` returns a single term). This is the column a UI
    -- facet should use: bouwen / ruimtelijke ordening / kappen / milieu / uitweg en inrit /
    -- slopen / natuur / reclame / brandveilig gebruik.
    -- An array because one permit can cover several activities; the sample had none, which is
    -- not enough to justify a scalar column.
    activiteit       text[] NOT NULL DEFAULT '{}',
    abstract         text,
    url              text NOT NULL,              -- preferredUrl, the human-readable page
    -- Everything not promoted to a column. The promoted set was chosen from a field census of
    -- real records, not from the schema docs: fields present in 50/50 became columns, the
    -- sparse ones stayed here.
    raw              jsonb NOT NULL DEFAULT '{}',

    first_seen_run   bigint NOT NULL REFERENCES etl_run (id),
    last_changed_run bigint NOT NULL REFERENCES etl_run (id)
);

-- The default query is "this area, this period", and the period is always a range, so the
-- date leads nothing on its own; it is paired with gemeente because that is the one filter
-- the API can push down without a spatial scan.
CREATE INDEX bekendmaking_gemeente_date ON bekendmaking (gemeente, published_at DESC);
CREATE INDEX bekendmaking_date ON bekendmaking (published_at DESC);
CREATE INDEX bekendmaking_type ON bekendmaking (doc_type);
CREATE INDEX bekendmaking_activiteit ON bekendmaking USING GIN (activiteit);


-- A publication is NOT a point: it is a SET of points, and modelling it as one column would
-- have silently dropped locations. Measured over 100 Amsterdam records: 80 have one point,
-- 11 have two, one has six (a splitsingsvergunning naming two addresses), and 8 have none.
-- The six-point record also repeats a coordinate, hence the unique constraint rather than a
-- surrogate key — `geometry` has a btree opclass, so the pair really can be enforced.
--
-- The address columns are nullable and genuinely sparse, and the sparseness is per
-- municipality, not per record type: all of a Diemen sample carried straatnaam/postcode while
-- only 1 of 50 Amsterdam records did. Anything that reads them must degrade to the geometry.
CREATE TABLE bekendmaking_locatie (
    identifier text NOT NULL REFERENCES bekendmaking (identifier) ON DELETE CASCADE,
    geom       geometry(Point, 4326) NOT NULL,
    label      text,                             -- w.geometrielabel, free text
    postcode   text,
    straatnaam text,
    huisnummer text,
    woonplaats text,

    CONSTRAINT bekendmaking_locatie_uniq UNIQUE (identifier, geom)
);

-- Plain GiST on the geometry alone, deliberately NOT the composite
-- `GIST(doc_type, geom)` that `poi` uses. `poi`'s composite earns its keep because every
-- /pois query carries a category predicate; the announcement queries are "polygon + date
-- range" and only sometimes carry a type. Measured on this session's isochrone-to-poi join,
-- a composite GiST cost 9,898 buffers against 7,699 for a geom-only one when the leading
-- column was absent from the predicate — 29% more for a column the query never uses.
CREATE INDEX bekendmaking_locatie_geom ON bekendmaking_locatie USING GIST (geom);

-- Lookups in the other direction (one publication -> its points) ride the unique constraint's
-- index, so no separate index on identifier.
