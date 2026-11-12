-- `UNIQUE (identifier, geom)` from 0005 cannot hold the real data.
--
--   index row size 6560 exceeds btree version 4 maximum 2704 for index
--   "bekendmaking_locatie_uniq"
--
-- A btree entry must fit in roughly a third of an 8 KB page, and a LINESTRING covering a
-- street section is a few thousand bytes of coordinates. The constraint survived the first
-- import only because that import was storing POINTs, which are 32 bytes — the bug in 0005
-- was hiding the limit that 0006's fix then walked straight into.
--
-- The fix is the one the server's own HINT suggests: index a hash instead of the value.
-- md5(ST_AsBinary(geom)) is fixed-width and ST_AsBinary is IMMUTABLE, so it can carry a
-- unique index. This is a dedup key, not a security boundary, so md5 is the right tool; a
-- collision would drop one duplicate location of one publication.
--
-- Note the semantics narrow slightly: the old constraint compared geometries with btree's
-- geometry ordering, the new one compares their exact binary serialisation. Two geometries
-- that are equal in shape but differ in vertex order or precision now count as distinct.
-- That is acceptable because the source repeats a geometry verbatim when it repeats it at
-- all — the six-point splitsingsvergunning in 0005's comment repeated an identical pair.
ALTER TABLE bekendmaking_locatie DROP CONSTRAINT bekendmaking_locatie_uniq;

CREATE UNIQUE INDEX bekendmaking_locatie_uniq
    ON bekendmaking_locatie (identifier, md5(ST_AsBinary(geom)));
