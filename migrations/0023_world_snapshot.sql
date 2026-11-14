-- The world a verdict was reached in, as a content-addressed snapshot.
--
-- 0021's claim_stale() compared the claim's recorded dimensions key by key against a
-- jsonb the caller assembled by hand. Three things went wrong with that shape, and all
-- three are the same shape as the failures this ledger exists to catch:
--
--   1. The caller had to know which keys matter. Nothing supplied them, so the function
--      was never called: 0 callers, 12 days, and the one real stale number was found by
--      hand anyway.
--   2. A key can be under-specified and look fine. Claim #3 recorded
--      `graph_date: 2026-10-28T16:45:20Z` -- which is cycling-regular's build date. The
--      three profiles have three different build dates (16:43:51 / 16:44:44 / 16:45:20).
--      Rebuild only driving-car and that key still matches: "not stale", wrongly.
--   3. A dimension that could not be read had to be distinguished from one that had not
--      changed, which is the whole of 0022. An unreachable ORS made every graph claim read
--      as current -- a check that silently stops checking, the same shape as a /metrics
--      that answers an empty 200.
--
-- A snapshot fixes all three by not having keys at all. A facet is probed, the whole probe
-- output is hashed, and that digest is the world's identity along that facet. Different
-- digest => untested, and nothing has to know why. "ORS unreachable" is a payload like any
-- other, so it hashes differently from "ORS answered" -- incompleteness becomes a visible
-- difference instead of a silent pass.
--
-- The cost, stated plainly: a digest is all-or-nothing where a key comparison was precise.
-- One imported row changes the corpus digest and every verdict anchored to `corpus` is
-- untested at once, including ones that had nothing to do with that row. That is why the
-- world is split into facets rather than hashed whole, and why `verdict_world` records
-- which facets a verdict actually depended on.

-- A closed set, for the same reason topic is (0024): free text lets `code` and `cdoe`
-- diverge, and a facet that is never probed again has a "current" digest equal to its own,
-- so it would never report stale. Silent. Adding a facet is an INSERT, not a code change.
CREATE TABLE world_facet (
    name        text PRIMARY KEY,
    probe       text NOT NULL,          -- the command, as run. Not a description of it.
    created_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO world_facet (name, probe) VALUES
    ('code',   'git rev-parse HEAD; git status --porcelain'),
    ('corpus', 'SELECT count(*), md5(string_agg(identifier)) FROM bekendmaking; counts of related tables'),
    ('graph',  'GET {ORS_BASE_URL}/status -> graph_build_date + osm_date per profile');

CREATE TABLE world_snapshot (
    id            bigserial PRIMARY KEY,
    facet         text NOT NULL REFERENCES world_facet (name),
    digest        text NOT NULL,
    payload       jsonb NOT NULL,
    -- first_seen_at is when this exact world state was first recorded; last_seen_at is the
    -- most recent probe that found it unchanged. Both are needed: with only first_seen_at,
    -- a world that goes A -> B -> A would leave A's row older than B's, and "the current
    -- digest for this facet" -- ordered by time -- would answer B. Wrongly, and silently.
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT world_snapshot_digest_form
        CHECK (digest ~ '^[0-9a-f]{12,64}$'),
    CONSTRAINT world_snapshot_payload_is_object
        CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT world_snapshot_seen_order
        CHECK (last_seen_at >= first_seen_at)
);

-- Re-probing an unchanged facet must not create a row: "the world changed" and "we looked
-- again" have to stay distinguishable. The upsert target.
CREATE UNIQUE INDEX world_snapshot_facet_digest ON world_snapshot (facet, digest);
CREATE INDEX world_snapshot_current ON world_snapshot (facet, last_seen_at DESC);

-- Which facets a verdict's evidence actually depended on. A verdict with no rows here can
-- never go stale -- that is a real state, not an error (a claim about this repo's own
-- conventions depends on no facet), and verdict_unanchored below makes it a queryable list
-- rather than something that merely looks current.
CREATE TABLE verdict_world (
    verdict_id  bigint NOT NULL REFERENCES verdict (id) ON DELETE CASCADE,
    snapshot_id bigint NOT NULL REFERENCES world_snapshot (id),
    PRIMARY KEY (verdict_id, snapshot_id)
);


-- A verdict needs a subject, so that "is anything I know about search stale" is a query.
--
-- A closed set again, and for a sharper reason than tidiness: with free text, `search` and
-- `Search` diverge and the staleness check for a topic silently covers nothing -- it returns
-- zero rows and zero rows reads exactly like "nothing is stale". Adding a topic is an INSERT.
CREATE TABLE topic (
    name       text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO topic (name) VALUES
    ('search'), ('bekendmaking'), ('routing'), ('poi'), ('perf'), ('process');

ALTER TABLE claim ADD COLUMN topic text REFERENCES topic (name);

CREATE INDEX claim_topic ON claim (topic);


-- The world as it is now: one row per facet, the digest most recently probed.
CREATE VIEW world_current AS
SELECT DISTINCT ON (facet) id, facet, digest, payload, first_seen_at, last_seen_at
FROM world_snapshot
ORDER BY facet, last_seen_at DESC, id DESC;


-- Every verdict whose world has moved under it, one row per facet that differs.
--
-- Takes no arguments, on purpose. claim_stale(jsonb) needed the caller to assemble the
-- current world, which is exactly why it was never called.
CREATE FUNCTION verdict_stale()
RETURNS TABLE (verdict_id bigint, claim_id bigint, topic text, claim_text text,
               verdict text, facet text, reached_under text, world_now text,
               reached_at timestamptz)
LANGUAGE sql STABLE AS $$
    SELECT v.id, c.id, c.topic, c.text, v.verdict, s.facet, s.digest, w.digest, v.at
    FROM verdict v
    JOIN claim c          ON c.id = v.claim_id
    JOIN verdict_world vw ON vw.verdict_id = v.id
    JOIN world_snapshot s ON s.id = vw.snapshot_id
    JOIN world_current w  ON w.facet = s.facet
    WHERE s.digest <> w.digest
    ORDER BY v.id, s.facet
$$;


-- Verdicts anchored to nothing, which therefore never expire.
--
-- This is the designed-in failure mode made visible. Getting the facet list wrong fails
-- toward over-reporting (noisy, but you see it); omitting it entirely fails toward silence,
-- so the silent case gets its own list. The three claims backfilled by hand in 0021 land
-- here and belong here: a corpus of 10,342 rows cannot be digested retroactively.
CREATE VIEW verdict_unanchored AS
SELECT v.id AS verdict_id, c.id AS claim_id, c.topic, c.text, v.verdict, v.at
FROM verdict v
JOIN claim c ON c.id = v.claim_id
WHERE NOT EXISTS (SELECT 1 FROM verdict_world w WHERE w.verdict_id = v.id)
ORDER BY v.id;
