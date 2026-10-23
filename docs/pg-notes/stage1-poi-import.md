# Stage 1 — raw notes: OSM POI import, index design, HOT and bloat

Cluster: PostgreSQL 16.4, `imresamu/postgis:16-3.4` (arm64), local Docker.
Params per `docker-compose.yml`: `shared_buffers=128MB`, `work_mem=4MB`,
`autovacuum_naptime=10s`, `track_io_timing=on`.

Data: `data/noord-holland-latest.osm.pbf` from Geofabrik, 181 MB, 2026-10-17.
Validation is on one province before the full country, per the stage 1 acceptance criteria.

## Import result

```
$ time python scripts/import_poi.py data/noord-holland-latest.osm.pbf
osm_poi:noord-holland: 47 tag rules
etl_run 1 claimed
  staged 50000
  staged 82259
staged 82259 objects, 0 without geometry
merge INSERT 0 82259, soft delete UPDATE 0
poi now has 82259 live rows
       20.4s total
```

82,259 POI from 47 tag rules. Distribution is heavily skewed, which is what makes the
selectivity experiment below have a real answer rather than a contrived one:

| category | rows |
|---|---|
| car_parking | 32786 |
| recycling | 6566 |
| bicycle_parking | 6493 |
| bus_stop | 6160 |
| playground | 5570 |
| restaurant | 3949 |
| … | |
| supermarket | 802 |

`car_parking` alone is 40% of the table; `supermarket` is 1%.

### 54% of the POI are ways, which the Overpass query could never return

```
 osm_type | count
----------+-------
 n        | 37886
 w        | 44373
```

`upstream/overpass.py` asks only for `node[...]`. Every supermarket, school or sports
centre mapped as a building outline — 44,373 objects here — was invisible to it. This is
independent of, and larger than, the circle-vs-isochrone bug in the same file.

Sanity checks: 0 rows outside the NL bounding box; extent 4.497–5.302 E, 52.168–53.181 N,
which is Noord-Holland; 27,867 of 82,259 have a `name`.

### The gym rule split, now visible as data

```sql
SELECT tags->>'leisure', tags->>'amenity', count(*) FROM poi WHERE category='gym' GROUP BY 1,2;
 fitness_centre | dojo |   1
 fitness_centre |      | 353
```

In Noord-Holland `amenity=gym` matches **zero** objects — every gym is
`leisure=fitness_centre`. The rule is still correct to keep (tagging varies by region),
but this is the kind of fact that was unobservable while the mapping lived in an f-string.

## Index design: composite `gist(category, geom)` beats a GiST + btree pair

Query shape is always "these categories, inside this polygon". Three strategies compared
on the same 1.2 km circle around Amsterdam Centraal. Buffers hit, `EXPLAIN (ANALYZE, BUFFERS)`:

| query | `GIST(geom)` + `btree(category)` | `GIST(category, geom)` |
|---|---|---|
| polygon only | 819 (Bitmap Index Scan on geom, 2817 index rows) | 897, same plan shape |
| `category='supermarket'` (1%) | 512 (planner picks the **btree**, 802 index rows → 16) | **29** (25 index rows → 16) |
| `category='car_parking'` (40%) | 819 (planner picks the **GiST**, 2817 index rows → 75) | **65** (90 index rows → 75) |
| `category IN (3 values)` | — | **81**, one index scan — but see the correction below |

Two things worth saying out loud:

1. **With the pair, the planner flips between the two indexes based on category
   selectivity.** At 1% it takes the btree and rechecks 802 geometries; at 40% it takes
   the GiST and rechecks 2817 rows against the category. Neither index can do both jobs,
   so one of the two predicates is always evaluated as a heap filter.
2. **The composite's win is not a cheaper recheck, it is a smaller candidate set.** The
   index itself returns 25 rows instead of 802 or 2817. That is 12–18x fewer buffers.

Sizes: composite 5568 kB, versus `GIST(geom)` 3408 kB + `btree(category)` 648 kB on a
freshly built table. The composite is bigger than either, smaller than maintaining both,
and replaces both.

### Correction: `IN (a,b,c)` does **not** put `category` in the Index Cond

The "one index scan" in that last row was read too generously. It is one index scan,
but the category predicate is not an *index condition* — only the geometry is:

```
-- category = ANY('{supermarket,convenience,gym}'::text[])   (identical to IN (...))
Index Scan using poi_cat_geom_gist on poi  (actual rows=52)
  Index Cond: (geom @ '...'::geometry)
  Filter: ((category = ANY (...)) AND st_contains('...'::geometry, geom))
  Rows Removed by Filter: 2337
  Buffers: shared hit=1708
```

versus a single-value equality on the same polygon:

```
-- category = 'supermarket'
  Index Cond: ((category = 'supermarket'::text) AND (geom @ '...'::geometry))
  Rows Removed by Filter: 9
  Buffers: shared hit=23
```

`IN (a,b,c)` and `= ANY($1::text[])` are the same node — a `ScalarArrayOpExpr` — and
btree_gist cannot take one as an index condition. A btree index *can* (it does an
"index scan with array keys"), so this is specific to GiST. Only a plain `=` is
pushed down, which means a multi-category query has to issue one equality per
category. Parameterised, that is `unnest` + `LATERAL`:

```sql
FROM unnest($2::text[]) AS c(category), shape
CROSS JOIN LATERAL (
    SELECT ... FROM poi
    WHERE deleted_at IS NULL AND category = c.category AND ST_Contains(shape.g, geom)
    LIMIT $3
) p
LIMIT $3
```

| form | plan | buffers |
|---|---|---|
| `= ANY($1::text[])` | `Index Cond: geom` only, 2337 rows removed | 1708 |
| `IN ('a','b','c')` | identical | 1708 |
| `unnest` + LATERAL, **no** inner `LIMIT` | flattened back to `Join Filter: c.category = poi.category`, 5570 removed | 1756 |
| `unnest` + LATERAL + inner `LIMIT` | `Index Cond: (category = c.category) AND (geom @ box)`, 8 removed per loop | **80** |

**The inner `LIMIT` is the load-bearing part, not the LATERAL.** Without it the
planner flattens the subquery and re-derives the join; with it the subquery cannot be
flattened, so it is re-executed once per category with `c.category` as a run-time
parameter — which is exactly what makes it an index condition. `WITH cats AS
MATERIALIZED (...)` was tried first on the theory that it would pin the join order:
it does nothing here, 1756 buffers.

### `ST_DWithin(geom::geography, …)` cannot use a `geometry` GiST at all

The degraded (no-isochrone) path is a radius in metres, so the obvious spelling is
`ST_DWithin(geom::geography, pt::geography, $r)`. The cast changes the operator class
and the distance term stops being indexable:

```
  Index Cond: (category = c.category)
  Filter: st_dwithin((geom)::geography, '...'::geography, '800'::double precision, true)
  Rows Removed by Filter: 533   (per loop)
  Buffers: shared hit=1445
```

Materialising the radius as a geodesic buffer *polygon* in 4326 and going back to
`ST_Contains` restores the two-column index condition, with the same 42 rows:

```sql
WITH shape AS (
    SELECT ST_Buffer(ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography, $4)::geometry AS g
)
```

```
  Index Cond: ((category = c.category) AND (geom @ '...'::geometry))
  Rows Removed by Filter: 1     (per loop)
  Buffers: shared hit=52
```

28x fewer buffers for an identical result set. `::geography` is still the right call
for *computing* distances; it is wrong as a *predicate* against a geometry index.

### The composite also serves a predicate on `geom` alone

With both indexes present the planner chose `poi_cat_geom_gist` for the polygon-only
query, and dropping `poi_geom_gist` inside a rolled-back transaction produced the same
plan with the same 2817 index rows and the same 897 buffers. So `GIST(geom)` is dead
weight and was removed from the migration.

Caveat recorded in the migration: a geom-only search on a composite GiST has to descend
through every category subtree, and 82k rows is not where that cost shows up. Re-measure
at country scale (expected 0.5–1M rows).

### No btree on `category` alone — measured, then deliberately omitted

A category-only aggregate is an **Index Only Scan with `Heap Fetches: 0` on both**:

```
btree(category) WHERE deleted_at IS NULL     4 buffers
GIST(category, geom) WHERE deleted_at IS NULL  65 buffers
```

16x, but no such query exists in the product yet. Noting it here instead of carrying the
index. (Also worth knowing: a GiST *can* do an index-only scan when the predicate and the
output only involve the non-lossy btree_gist column. It is the PostGIS geometry part that
is lossy, not GiST as such.)

### GIN `jsonb_path_ops` on `tags`

```sql
SELECT count(*) FROM poi WHERE deleted_at IS NULL AND tags @> '{"wheelchair":"yes"}';
 -> Bitmap Index Scan on poi_tags_gin  rows=4041  Buffers: shared hit=7
    Execution Time: 2.5 ms
```

4,041 rows, 7 buffers for the index scan. Index size 8648 kB, the largest on the table.

## HOT: the partial-index-predicate rule, and where its obvious form is wrong

`etl_run_one_active` is `UNIQUE (source) WHERE finished_at IS NULL`, predicated on a
write-once column rather than on the high-churn `status`, on the theory that updating any
indexed column — including one used only in a partial index predicate — defeats HOT.

Measured with `pg_stat_force_next_flush()` between statements (without it the counters
read 0 and the whole experiment looks broken):

```
3 UPDATEs touching only note / row_count   n_tup_upd=3  n_tup_hot_upd=3
the terminal UPDATE (status, finished_at)  n_tup_upd=4  n_tup_hot_upd=3
```

So the rule is real — non-indexed-column updates are 3/3 HOT — but the conclusion I had
written into the migration was wrong. The `running -> succeeded` update **cannot** be HOT
under either predicate, because that update is precisely the one that writes
`finished_at`. Choosing `finished_at` over `status` buys nothing for the terminal update;
it only protects updates that are *not* terminal, and leaves room for an intermediate
status later. Today `etl_run_finished_iff_terminal` rules such a status out, so the two
predicates are equivalent in practice. Comment corrected in `0003_poi.sql`.

## Bloat: an idempotent refresh is not a free refresh

First version of the merge bumped `last_seen_run` on every row it saw, so the refresh
could find rows it had *not* seen and soft-delete them. One **no-op** re-import of the
same file:

```
n_tup_ins=82259  n_tup_upd=82259  n_tup_hot_upd=0

poi             22 MB  ->  44 MB
poi_geom_gist  3408 kB -> 6760 kB
poi_tags_gin   8648 kB -> 10008 kB
poi_pkey             -> 5128 kB
```

Autovacuum ran and `n_dead_tup` returned to 0, but the space stays as free space inside
the relation and the index pages are not de-bloated by plain VACUUM. Nothing about the
data changed; the table doubled.

Zero of the 82,259 updates were HOT, which is expected: `geom` and `last_seen_run` are
both indexed, so every row needed new index entries.

**Fix** (both parts are needed, either alone does nothing):

1. `ON CONFLICT … DO UPDATE … WHERE` the row actually differs — category, name, geom,
   tags, or a non-null `deleted_at`.
2. Detect disappearance by anti-joining `poi_import` instead of looking for a stale run
   id, so unchanged rows need no write at all. `last_seen_run` is renamed
   `last_changed_run` to match what it now means, and its index is dropped because the
   refresh no longer scans it.

After:

```
$ python scripts/import_poi.py data/noord-holland-latest.osm.pbf   # second run
merge INSERT 0 0, soft delete UPDATE 0

n_tup_upd=0  n_tup_hot_upd=0  n_dead_tup=0
poi 22 MB, poi_cat_geom_gist 5568 kB   (unchanged)
```

The staging index `poi_import_key (osm_type, osm_id)` is built after the COPY rather than
declared in the migration, so it is not maintained row by row during the load.

## Refresh semantics verified

| behaviour | check | result |
|---|---|---|
| idempotent | import the same file twice | `INSERT 0 0`, 0 updates, 0 dead tuples |
| soft delete | remove 10 objects from `poi_import`, run the delete pass | 10 rows get `deleted_at`, leave the live partial index |
| resurrect | re-import the full file | exactly 10 rows updated, `first_seen_run` still 1, `last_changed_run` → 3 |
| scoped delete | delete pass is `ST_Intersects` with the staged extent | importing one province cannot touch another |
| sentinel | fake a previous run with `row_count=300000`, import 82,259 | run marked `failed`, exit 1, `poi` untouched |
| concurrent run | second claim for the same source | rejected by `etl_run_one_active` |

Sentinel output:

```
sentinel: staged 82259 vs 300000 last run — refusing to write a diff
exit=1
```

## Incidental: `bigserial` is not gapless

`etl_run` ids 2 and 4 were consumed by inserts that failed on a constraint, and are gone
for good. Relevant because a settlement-batch-number style requirement ("no gaps") cannot
be met by a SEQUENCE at all — it needs a single-row counter taken `FOR UPDATE`.

## Switching `/pois` over: the superset claim, measured

`/pois` now answers from `poi`, filtered by the *actual* isochrone polygon in
Postgres instead of by a circle upstream and `turf.booleanPointInPolygon` in the
browser. Both routers go through `domains/geo/service.py`, which also owns
`/isochrone`, because `/pois` has to read the polygon that `/isochrone` cached and
the two key formats had silently diverged — `iso:` at 5 decimals, `pois:` at 3, so
the shared read would have missed every time. The format is now one helper,
`cache.geo_cache_key`.

**Part 1 — behaviour is preserved.** Same centre (Amsterdam Centraal), same 800 m
circle, same three legacy categories:

| | elements | types |
|---|---|---|
| Overpass, node-only | 42 | 42 nodes |
| Postgres | 42 | 42 nodes |

Set difference in both directions is empty. So the migration is not a change in
what the default view shows.

**Part 2 — and that is the whole point of the measurement.** The "54% of POI are
ways" figure does *not* apply to the legacy three. Ways are 14 of 1638 rows there:

```
 category    | nodes | ways
 convenience |   481 |    4
 gym         |   347 |    7
 supermarket |   799 |    3
```

The node-only query lost almost nothing *for the categories it asked for*. The loss
is in the 34 categories it could not ask for at all, and those are exactly the
way-mapped ones:

```
 category        | nodes | ways  | % ways
 car_parking     |   656 | 32130 |   98.0
 park            |     7 |   806 |   99.1
 playground      |  1008 |  4562 |   81.9
 school          |   491 |   958 |   66.1
 swimming_pool   |    28 |  1145 |   97.6
 bicycle_parking |  3066 |  3427 |   52.8
```

`?categories=school&categories=park&categories=playground&categories=car_parking`
on the same circle returns 96 elements, **84 of them ways** — 87.5% of a result set
that the old endpoint could not have produced at any radius.

So the honest statement is not "Postgres returns a superset of Overpass for the same
query"; it is "the two agree on the query the old code could express, and the old
code could not express the queries where geometry type actually matters."

### Verified

| behaviour | check | result |
|---|---|---|
| default categories | no `categories` param | identical 42 rows to Overpass |
| explicit categories | 4 way-heavy categories | 96 rows, 84 ways |
| polygon path | synthetic L-shaped GeoJSON | 52 rows, `Index Cond` on both columns, 80 buffers |
| circle degradation | ORS 504 | logs `shape: circle`, still a Postgres answer |
| no Postgres | `db.DATABASE_URL = None` | falls through to Overpass, 42 rows, `type: "node"` |
| `type` spelling | both paths | `node` / `way`, mapped from `char(1)` on the PG side |

`ST_SetSRID` around `ST_GeomFromGeoJSON` is not decoration: GeoJSON without a `crs`
member yields SRID 0, and `ST_Contains` then refuses the mixed-SRID comparison.

### An invalid polygon is answered, not rejected

The polygon is whatever ORS computed, and an isochrone is a concave hull, so a
self-intersecting ring is a shape ORS can emit. `ST_GeomFromGeoJSON` accepts one
without complaint, and `ST_Contains` then returns an **undefined** answer rather than
an error — a bowtie ring over this data produced 28 rows and a single
`NOTICE: Self-intersection at or near point 4.8999999999999995 52.377499999999998`.
Nothing in the response or the logs would have shown it.

```sql
SELECT ST_CollectionExtract(
           ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON($1), 4326)), 3) AS g
```

`ST_CollectionExtract(..., 3)` is required because MakeValid on a bad ring yields a
MultiPolygon or a GeometryCollection, not a Polygon. The guard is free on a good
polygon — same 52 rows, same 80 buffers, 0.39 ms — because it runs once on the shape,
not once per candidate row. Verified against three inputs: a valid Polygon (52 rows),
the bowtie (40 rows, now a defined answer), and a two-part MultiPolygon (57 rows),
the last being what ORS returns if more than one interval is ever requested.

Vertex count, by contrast, is a non-issue: densifying the same 800 m shape from 33 to
321 points left both the buffer count (100) and the runtime unchanged, because GEOS
builds a prepared geometry once per shape rather than walking the ring per row.

The Postgres answer is deliberately **not** written to Redis. It is one index scan on
a local table (0.3–0.4 ms), and a cached copy would only go stale the next time
`import_poi.py` runs. The `pois:` key now exists solely for the Overpass path.

## Open

- **The isochrone-shaped path has not been exercised against a real ORS polygon.**
  ORS returned 504 on six consecutive attempts over fifteen minutes from this IP, so
  every live `/pois` call fell back to the circle. The polygon SQL was verified with a
  hand-written GeoJSON polygon through the identical code path, but the end-to-end
  shape is unconfirmed.
- Upstash was also unreachable from this machine (DNS failure), so `/pois` made a
  fresh ORS call per request. With the cache working the frontend's parallel
  `/isochrone` + `/pois` pair costs one ORS call, as before; without it, two.
- `MAX_POIS = 2000` is applied per category *and* overall (inner and outer `LIMIT`),
  so a request for several unselective categories is capped but not fairly shared.
- Relations are skipped by the importer. A POI mapped as a multipolygon relation (some
  large parks, hospital grounds) is missed. Needs pyosmium's area handling, which is a
  second pass over the file.
- The composite-vs-pair conclusion for the polygon-only query needs re-measuring at full
  country scale.
- `poi_import` is 31 MB of staging for 22 MB of table and is left populated after a run.
  Harmless (UNLOGGED, truncated at the start of the next run) but it is not free disk.

---

# Re-measured at country scale, 2026-10-18

`data/netherlands-latest.osm.pbf` (1.3 GB) replaces the province extract. Same cluster,
same parameters, `ANALYZE poi` before measuring, second (warm) run reported.

```
staged 512844 objects, 0 without geometry
merge INSERT 0 430707, soft delete UPDATE 17
poi now has 512844 live rows
       2m52.9s total
```

6.2x the rows for 8.5x the wall clock. The 17 soft deletes are the Noord-Holland rows whose
OSM object disappeared between the two extracts, and they are the only evidence that the
bbox-scoped delete pass works across two differently-shaped imports.

Immediately re-running it is still a true no-op at this size:

```
merge INSERT 0 0, soft delete UPDATE 0      1m58.3s
```

Zero writes out of 512,844 rows. The second run is a minute faster than the first because the
only difference is the writes it no longer does.

## Index sizes now

| index | size | idx_scan |
|---|---|---|
| `poi_cat_geom_gist` | 34 MB | 187 |
| `poi_pkey` | 24 MB | 1,354,730 |
| `poi_tags_gin` | 26 MB | 8 |

`poi_tags_gin` is still the index nothing queries — 8 scans, all of them mine. 6.2x the rows
grew it 3x (8648 kB -> 26 MB), so it is no longer the largest index, but it is still 26 MB
maintained on every write for a query path that does not exist.

## The `= ANY` finding does NOT survive the scale change

This is the correction that matters. At 82,259 rows `category = ANY($1)` fell to a heap
filter (1708 buffers) and the `unnest` + `LATERAL` rewrite was 21x better (80). At 512,844
rows, same query, same index:

| form | plan | buffers |
|---|---|---|
| `= ANY('{supermarket,convenience,gym}')` | **Bitmap** Index Scan, `Index Cond: ((category = ANY (...)) AND (geom @ box))` | **91** |
| `unnest` + LATERAL + inner `LIMIT` | Nested Loop, 3x Bitmap Index Scan, `Index Cond: ((category = c.category) AND ...)` | 98 |

The array is now **inside the Index Cond**, which the 82k measurement said was impossible.
Both return the same 73 rows.

The earlier explanation was half right. `pg_am.amsearcharray` is false for GiST, so a *plain*
Index Scan cannot take a `ScalarArrayOpExpr`. But a **Bitmap** Index Scan does not need the
AM to support arrays — the executor runs one index scan per array element and ORs the
bitmaps together. At 82k rows the planner costed a plain Index Scan cheaper and the array
fell out to a filter; at 512k it switches to a bitmap plan and the array is pushed back down.

So the rewrite's win was **a property of the plan the planner happened to choose, not a
property of GiST.** It is scale-dependent, and at the current size it is marginally *worse*
(98 vs 91 — three bitmap scans instead of one). The `unnest` form is still what `geo/service.py`
issues; it is not wrong, and it is immune to the planner flipping back at a different size,
but the "21x" number must not be quoted as if it were a law. Recite this as: *measured it,
believed the explanation, re-measured at 6x the data and the effect inverted.*

## What did hold, and got stronger

`ST_DWithin(geom::geography, …)` against a `geometry` GiST, 800 m radius, 3 categories:

| form | buffers | time |
|---|---|---|
| `ST_DWithin(geom::geography, …)` | **6580** (1900 hit + 4680 **read**) | 247 ms |
| `ST_Buffer(pt::geography, r)::geometry` + `ST_Contains` | **62** | 0.45 ms |

106x the buffers and 550x the time, same 42 rows. At 82k it was 1445 vs 52 and everything was
cached; at 512k the filtered-out candidate set (3074 rows removed per loop) no longer fits in
`shared_buffers`, so 4680 of those buffers are real reads. **The bad predicate degrades
superlinearly** — it loses cache residency before it loses anything else.

## Single-category and polygon-only

| query | 82k | 512k | note |
|---|---|---|---|
| polygon only (1.2 km circle) | 897 | 892 | now a Parallel Bitmap Heap Scan; index part 148 buffers for 2776 index rows |
| `category='supermarket'` | 29 | 28 | plain Index Scan, 19 rows |
| `category='car_parking'` | 65 | 99 | parallel bitmap, 205 index rows |

Polygon-only did not move, because the bbox limits the candidate set and the country extract
adds rows *outside* that bbox. This answers the open question left at 82k ("re-measure the
composite-vs-pair conclusion at country scale"): a geom-only search on a composite GiST does
not degrade at 6x the rows, so the decision to drop `GIST(geom)` still stands.
