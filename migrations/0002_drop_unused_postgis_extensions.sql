-- The PostGIS image's initdb script installs postgis_tiger_geocoder and
-- postgis_topology alongside postgis. Together they create ~35 tables (a US
-- TIGER address geocoder and a topology schema), none of which this project uses.
--
-- They are dropped because pg_stat_user_tables is read constantly during the
-- later stages to watch n_dead_tup and autovacuum, and 35 permanently-empty
-- rows bury the handful of tables that matter.
DROP EXTENSION IF EXISTS postgis_tiger_geocoder CASCADE;
DROP EXTENSION IF EXISTS postgis_topology CASCADE;
