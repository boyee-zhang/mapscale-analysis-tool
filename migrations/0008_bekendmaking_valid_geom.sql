-- 4 of the first 495 imported POLYGONs are self-intersecting rings. PostGIS says so only as
-- a NOTICE, and ST_Contains against an invalid polygon returns an UNDEFINED answer rather
-- than raising — the identical trap documented for ORS isochrones in
-- docs/pg-notes/stage1-poi-import.md, where a bowtie ring returned 28 rows and one NOTICE.
--
-- domains/geo/service.py repairs the isochrone at query time because that polygon is
-- transient. These rows are stored, so the repair belongs at write time: paid once, and the
-- constraint then makes "this table cannot hold a geometry that gives undefined answers" an
-- invariant the database enforces rather than a convention the importer remembers.
--
-- Only polygonal geometry can be invalid here; POINT and LINESTRING always validate, so the
-- importer's repair is a no-op for the 1,142 of 1,637 rows that are not polygons.
UPDATE bekendmaking_locatie
   SET geom = ST_CollectionExtract(ST_MakeValid(geom), 3)
 WHERE NOT ST_IsValid(geom);

ALTER TABLE bekendmaking_locatie
    ADD CONSTRAINT bekendmaking_locatie_geom_valid CHECK (ST_IsValid(geom));
