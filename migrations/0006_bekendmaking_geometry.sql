-- Correction to 0005, found by comparing the imported rows against the source rather than
-- by trusting the import's own counts.
--
-- 0005 modelled a publication's geography as Point, because `w.locatiepunt` is the index the
-- SRU service exposes for spatial search and every record in the first sample carried one.
-- That field is only the POINT SUBSET. The complete geography is `locatiegebied`, and over
-- 100 Diemen records from 2026 it holds three different shapes:
--
--     LINESTRING(...)       79   a street section — e.g. a traffic decision
--     POLYGON((...))        53   a parcel or site — e.g. an omgevingsvergunning
--     "lat,lon"             48   a bare point, and note the axis order is REVERSED
--                                relative to the WKT forms, which are lon lat
--
--     locatiepunt present   46 / 100
--     locatiegebied present 100 / 100
--
-- So the first import silently dropped the geography of 54 of every 100 publications while
-- reporting success, and `omgevingsvergunning` came out at 14% geo-coded when the real figure
-- for that sample is 100%. The same mistake invalidates the "57% of all records are
-- geo-coded" figure measured through `w.locatiepunt within/...`: that counts point records,
-- not located records.
--
-- Two lessons worth keeping, both already in this repo's notes under other names: an import
-- that under-reports looks exactly like sparse upstream data, so verify a sample against the
-- source; and the field a search index is built on is not necessarily the field that holds
-- the data.
ALTER TABLE bekendmaking_locatie
    ALTER COLUMN geom TYPE geometry(Geometry, 4326);

-- Cheap to record, and it makes the mixed-type column self-describing in \d output.
ALTER TABLE bekendmaking_locatie
    ADD CONSTRAINT bekendmaking_locatie_geom_kind
    CHECK (GeometryType(geom) IN ('POINT', 'LINESTRING', 'POLYGON', 'MULTIPOLYGON', 'MULTILINESTRING'));

-- The GiST index from 0005 covers any geometry type and needs no change; ST_Contains and
-- ST_Intersects against the isochrone work the same for all three shapes. What DOES change is
-- that a clip must use ST_Intersects rather than ST_Contains if a street-long LINESTRING
-- crossing the isochrone boundary should count as inside it.
