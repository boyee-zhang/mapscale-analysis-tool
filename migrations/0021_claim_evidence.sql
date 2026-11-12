-- Claims, the evidence under them, and the verdicts that narrowed them.
--
-- This repo keeps its findings in docs/pg-notes/, which is right for a human reader and
-- useless for a question like "which of these numbers is still true". Two cases from this
-- week make the gap concrete:
--
--   * `a publication carries 2.5 geometries on average` was measured at 10,342 building
--     permits. The national import took the corpus to 94,183 and the number was never
--     re-measured; it stood in CLAUDE.md for twelve days and the real figure is 1.72.
--   * `the plausibility check should key on reachfactor, threshold > 0.15` survived review,
--     a stratified sample and a write-up before a second pass found that healthy driving-car
--     reachfactor is 0.0010-0.0993 — the gate would have degraded every driving isochrone in
--     the graph, Amsterdam included.
--
-- Neither was a careless mistake. Both were correct when measured and went stale without
-- anything saying so. That is what these three tables are for.
--
--
-- Why `context` is jsonb and not columns
-- --------------------------------------
-- The temptation is a generic log: command, output path, timestamp. That is exactly the
-- wrong shape, because the part that decides when a claim expires is the part a generic
-- schema cannot hold. Compare what a verdict actually says:
--
--   "On the self-hosted ORS v10.1.0 Noord-Holland graph (graph_date 2026-10-28T16:45:20Z,
--    osm_date 2026-10-16T20:21:21Z), measured over 131 isochrone responses (13 coordinates
--    x 3 profiles x ranges 120-3600 s)"
--
-- A generic schema stores `measured 2026-11-12` and drops every field that matters. Rebuild
-- the graph and graph_date moves; that claim is now untested and nothing in a timestamp says
-- so. So the domain dimensions go in jsonb — corpus_size, graph_date, coordinate, profile,
-- n_responses, whatever the measurement actually varied — and `claim_stale()` below asks the
-- only question worth asking: which recorded dimensions no longer match the world.
--
-- The same reasoning says this table should NOT be made generic and lifted out of the
-- project. Its value is the coupling. What IS generic is the contract — the four inputs the
-- falsifier demands and the verdict shape it returns — and that already lives in
-- .claude/agents/falsifier.md, which depends on none of this.
--
--
-- Why the constraints are shaped this way
-- ---------------------------------------
-- `evidence_path_needs_hash`: a row that names a file but no digest is a row that will lie.
-- Scratch files get overwritten — this session's own raw.json was re-run over twice — and a
-- path with no hash still points at something afterwards, so the record reads as supported
-- when the bytes behind it are gone. Either record the digest or do not name the file.
--
-- `claim_superseded_iff_pointed`: the same discipline as etl_run's
-- `etl_run_finished_iff_terminal`. A claim marked superseded with nothing to point at is a
-- dead end; whatever replaced it is the only useful thing to record.
--
-- `verdict_scope_unless_refused`: DESTROYED and SURVIVED must carry the narrowest surviving
-- form of the claim, because that line is the deliverable — it is what gets pasted into the
-- notes instead of the original wording. REFUSED carries none because nothing was tested.
--
-- Nothing in api/ reads these tables. They are a development surface, so `db.enabled()`
-- degradation does not apply to them.

CREATE TABLE claim (
    id            bigserial PRIMARY KEY,
    text          text NOT NULL,
    made_by       text NOT NULL,
    made_at       timestamptz NOT NULL DEFAULT now(),
    status        text NOT NULL DEFAULT 'open',
    -- The measurement's own dimensions. Keys are per-claim on purpose; see above.
    context       jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- The narrowest form that survived. NULL until something has tried to break it.
    scope         text,
    superseded_by bigint REFERENCES claim (id),

    CONSTRAINT claim_status_check
        CHECK (status IN ('open', 'survived', 'destroyed', 'superseded')),
    CONSTRAINT claim_superseded_iff_pointed
        CHECK ((status = 'superseded') = (superseded_by IS NOT NULL)),
    CONSTRAINT claim_no_self_supersede
        CHECK (superseded_by IS DISTINCT FROM id),
    CONSTRAINT claim_context_is_object
        CHECK (jsonb_typeof(context) = 'object')
);

CREATE TABLE evidence (
    id          bigserial PRIMARY KEY,
    claim_id    bigint NOT NULL REFERENCES claim (id) ON DELETE CASCADE,
    -- The exact command, as run. Not a description of it.
    command     text NOT NULL,
    output_path text,
    sha256      text,
    measured_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT evidence_sha256_form
        CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT evidence_path_needs_hash
        CHECK (output_path IS NULL OR sha256 IS NOT NULL)
);

CREATE TABLE verdict (
    id        bigserial PRIMARY KEY,
    claim_id  bigint NOT NULL REFERENCES claim (id) ON DELETE CASCADE,
    agent     text NOT NULL,
    verdict   text NOT NULL,
    scope     text,
    at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT verdict_value
        CHECK (verdict IN ('DESTROYED', 'SURVIVED', 'REFUSED')),
    CONSTRAINT verdict_scope_unless_refused
        CHECK (verdict = 'REFUSED' OR scope IS NOT NULL)
);

-- Lookups are "the open claims" and "everything about this claim", not scans.
CREATE INDEX claim_context_gin ON claim USING gin (context);
CREATE INDEX claim_live ON claim (made_at DESC) WHERE status IN ('open', 'survived');
CREATE INDEX evidence_claim ON evidence (claim_id);
CREATE INDEX verdict_claim ON verdict (claim_id, at DESC);


-- Which recorded dimensions no longer match the world.
--
-- Deliberately knows nothing about what the keys mean: any key a claim recorded, that the
-- caller also supplies, whose value now differs, is reported. That is what makes it catch the
-- next stale number rather than only the two we already know about. Had the 2.5-geometries
-- claim recorded {"corpus_size": 10342}, this would have flagged it the day the national
-- import landed, instead of twelve days later by hand.
--
--   SELECT * FROM claim_stale(jsonb_build_object(
--       'corpus_size', (SELECT count(*) FROM bekendmaking),
--       'graph_date',  '2026-10-28T16:45:20Z'));
CREATE FUNCTION claim_stale(now_ctx jsonb)
RETURNS TABLE (claim_id bigint, claim_text text, dimension text,
               measured jsonb, current_value jsonb)
LANGUAGE sql STABLE AS $$
    SELECT c.id, c.text, k, c.context -> k, now_ctx -> k
    FROM claim c, jsonb_object_keys(c.context) AS k
    WHERE c.status IN ('open', 'survived')
      AND now_ctx ? k
      AND c.context -> k IS DISTINCT FROM now_ctx -> k
    ORDER BY c.id, k
$$;
