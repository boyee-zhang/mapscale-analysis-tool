# Stage 0 — raw notes

Cluster: PostgreSQL 16.4, `imresamu/postgis:16-3.4` (arm64), local Docker.
Params per `docker-compose.yml`: `shared_buffers=128MB`, `work_mem=4MB`,
`autovacuum_naptime=10s`, `track_io_timing=on`.

Data: `municipality` = 342 rows (PDOK CBS WFS), `geocode_cache` = 6 rows.

## Platform: the official PostGIS image is amd64-only

`docker manifest inspect postgis/postgis:16-3.4` lists only `amd64`. On Apple Silicon
that runs under emulation, which makes every `EXPLAIN ANALYZE` timing meaningless in
absolute terms. Switched to `imresamu/postgis:16-3.4`, which publishes arm64 and is a
drop-in (same entrypoint and env vars). `ghcr.io/baosystems/postgis:16-3.4` is another
multi-arch option.

Side effect worth knowing: the data directory records the glibc collation version at
`initdb` time. Initialising on amd64 (glibc 2.31) then starting on arm64 (glibc 2.36)
produces `database "mapscale" has a collation version mismatch` on every connection.
Switching base images therefore means re-initialising the volume, not just pulling.

## `ST_Contains` point-in-polygon — GiST vs Seq Scan

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT code, name FROM municipality
WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(4.9041, 52.3676), 4326));
```

With the index:

```
 Index Scan using municipality_geom_gist on municipality  (cost=0.14..20.66 rows=1 width=17) (actual time=0.337..0.339 rows=1 loops=1)
   Index Cond: (geom ~ '0101000020E6...'::geometry)
   Filter: st_contains(geom, '0101000020E6...'::geometry)
   Buffers: shared hit=53
 Execution Time: 0.462 ms
```

Same query with `SET enable_indexscan = off; SET enable_bitmapscan = off;`:

```
 Seq Scan on municipality  (cost=0.00..4368.42 rows=1 width=17) (actual time=0.481..1.212 rows=1 loops=1)
   Filter: st_contains(geom, '0101000020E6...'::geometry)
   Rows Removed by Filter: 341
   Buffers: shared hit=99
 Execution Time: 1.345 ms
```

Two things to take away.

**1. The `Index Cond` / `Filter` split is the GiST two-stage search.**
`geom ~ <box>` is the bounding-box overlap test answered from the index. `st_contains`
then runs as a recheck on the few candidates that survive. The index never answers the
spatial predicate itself — it only cheapens the candidate set.

**2. The planner prefers the index on a 342-row table because of function cost, not table size.**
The heap is 720 kB / 99 pages, so the I/O difference is negligible. The estimated cost gap
is 20.66 vs 4368.42 — a factor of 211 — and it comes almost entirely from evaluating
`st_contains` 342 times. PostGIS declares a high `procost` for exactly this reason. A
non-spatial predicate on 342 rows would have gone to a Seq Scan without hesitation.

Sizes: total 888 kB, heap 720 kB, `municipality_geom_gist` 24 kB.

## `geocode_cache` batch read uses a Seq Scan — and that is correct

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT address_norm, ST_Y(geom), ST_X(geom) FROM geocode_cache
WHERE address_norm = ANY(ARRAY['spui 1 s gravenhage','kastanjelaan 1 eindhoven']);
```

```
 Seq Scan on geocode_cache  (cost=0.00..17.64 rows=2 width=48) (actual time=0.022..0.023 rows=2 loops=1)
   Filter: (address_norm = ANY ('{...}'::text[]))
   Rows Removed by Filter: 4
   Buffers: shared hit=1
 Execution Time: 0.060 ms
```

The primary-key index exists and is ignored: the whole table is one page, so an index
scan would read strictly more pages than the heap scan. Re-run this once the table has
grown — the plan should flip to a Bitmap Index Scan, and that flip is the observation
worth capturing, not the current plan.

Deliberate omission, to revisit: `geocode_cache` has **no** spatial index on `geom`. It
is only ever point-queried by primary key. Adding GiST would cost write throughput on
the hottest write path in the stage for zero read benefit.

## Buffers are all `hit=`, no `read=`

The plan predicted `shared_buffers=128MB` would expose real disk reads. At 888 kB it
obviously does not — everything is resident after the first touch. The only `read=`
observed in this stage was in *planning* buffers on the very first query of a fresh
connection (`Buffers: shared hit=101 read=9 dirtied=2`), i.e. catalogue lookups, not
table data. Disk reads will need stage 3's row counts.

Also noted: planning time on the first `ST_Contains` of a session was 23.5 ms against an
execution time of 0.46 ms — PostGIS operator and function resolution dominates until the
catalogue is cached.

## Migration runner

`python scripts/migrate.py` twice → `applied 1 migration(s)` then `nothing to apply`.
`--status` prints `0001  applied   0001_init.sql`.

Caveat inherent to the `.noxact.sql` convention (unused so far): a file run outside a
transaction that fails partway leaves the schema changed and *unrecorded*, so the next
run retries it from the top. `CREATE INDEX CONCURRENTLY` is the motivating case, and it
is also the one that leaves an `INVALID` index behind on failure. Any future `.noxact`
migration must be written to tolerate re-running.
