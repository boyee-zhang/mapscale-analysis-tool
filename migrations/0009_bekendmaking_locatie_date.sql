-- Denormalise published_at onto the location rows, and index it ahead of the geometry.
--
-- 0005 chose a geom-only GiST and justified it with the poi measurement, where a composite
-- `GIST(category, geom)` cost 29% more buffers than a plain one. That reasoning does not
-- transfer, and the number says so. The poi query's leading column was ABSENT from the
-- predicate; every announcement query carries a date range, because "what happened here" is
-- always asked of a period.
--
-- Measured on 88,877 location rows against a real 15-minute cycling isochrone, asking for
-- publications since 2026-07-01 grouped by type. All figures AFTER `VACUUM FULL`, because the
-- first attempt at this comparison did not control for bloat and credited the index with it:
-- this table had been through a failed import, a TRUNCATE and three reloads and sat at 44 MB
-- against 24 MB packed, which is most of a 2x on every buffer count.
--
--   date filter on bekendmaking (as built in 0005)        13,936 buffers   66.1 ms
--   date on the location row + GIST (published_at, geom)  11,704 buffers   41.0 ms
--
-- 1.6x faster, 16% fewer buffers, because both predicates reach the index:
--
--   Index Cond: ((published_at >= '2026-07-01') AND (geom && i.geom))
--
-- instead of one of them as a heap Filter. The gain is much larger when the query does not
-- have to join back for doc_type — the same clip asking only for a count is 4,128 buffers and
-- 12.9 ms, a 3.4x — so the remaining cost is the hash join against all 54,566 publications,
-- not the spatial scan.
--
-- Denormalising doc_type as well was measured at 3,980 buffers / 14.8 ms for the grouped
-- query, another ~3x. NOT done here: it is a second copied column with the same maintenance
-- obligation, doc_type is likelier than a publication date to be corrected upstream, and
-- 41 ms at this size does not demand it. Recorded so the option is a decision, not a
-- rediscovery.
--
-- Two things this does NOT say. It does not say "composite GiST is good": 0005 preferred a
-- plain one on this very table, citing poi, where a composite cost 29% MORE. Both are right —
-- poi's leading column was absent from the predicate and this one is always present. And it
-- does not say "denormalise": the denormalised column with a geom-only index measured 15,080
-- buffers, slightly WORSE than not denormalising at all, because a wider row fits fewer
-- tuples per heap page and the filter saved nothing the index could use.
-- The rule is narrower than either slogan: a copied column pays only when an index can use
-- it, and a composite index pays only when its leading column is in the predicate.
--
-- One more number worth keeping: the new index does not stay resident. The 11,704 above is
-- 7,779 hit + 3,925 read, repeatably, against shared_buffers=128MB — which this project set
-- deliberately low so effects like this appear at 10^5 rows instead of 10^9.

ALTER TABLE bekendmaking_locatie ADD COLUMN published_at date;

UPDATE bekendmaking_locatie l
   SET published_at = b.published_at
  FROM bekendmaking b
 WHERE b.identifier = l.identifier;

ALTER TABLE bekendmaking_locatie ALTER COLUMN published_at SET NOT NULL;

CREATE INDEX bekendmaking_locatie_date_geom
    ON bekendmaking_locatie USING GIST (published_at, geom);
DROP INDEX bekendmaking_locatie_geom;

-- A copied column is a lie waiting to happen, so the copy is maintained by the database
-- rather than by whoever remembers to. In practice dt.date is a publication date and does not
-- move, but "in practice" is not an invariant — this is the same preference as the
-- CHECK (ST_IsValid(geom)) added in 0008: enforce it where it cannot be forgotten.
CREATE FUNCTION bekendmaking_sync_locatie_date() RETURNS trigger AS $$
BEGIN
    UPDATE bekendmaking_locatie
       SET published_at = NEW.published_at
     WHERE identifier = NEW.identifier
       AND published_at IS DISTINCT FROM NEW.published_at;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER bekendmaking_date_to_locatie
    AFTER UPDATE OF published_at ON bekendmaking
    FOR EACH ROW WHEN (OLD.published_at IS DISTINCT FROM NEW.published_at)
    EXECUTE FUNCTION bekendmaking_sync_locatie_date();
