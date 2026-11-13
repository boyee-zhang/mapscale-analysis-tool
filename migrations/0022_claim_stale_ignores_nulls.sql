-- claim_stale(): a dimension that was never measured must not expire a claim.
--
-- 0021's version compared every key the claim and the caller share. The first real run
-- flagged a claim that was perfectly current:
--
--   #2 tussenkop is 0.60-0.69% of stored body characters
--        graph_date: measured null  ->  now "2026-10-28T16:45:20Z"
--
-- That claim has nothing to do with the routing graph. Its context carried
-- `"graph_date": null` to say so, and `jsonb null IS DISTINCT FROM "2026-10-28..."` is true,
-- so it was reported as stale. One false positive in the first three rows.
--
-- This matters more than the arithmetic: the whole point of this function is to be trusted
-- enough that someone acts on its output. A staleness report with noise in it gets skimmed,
-- and then the one real entry — #1, measured at 10,342 rows against today's 94,183 — is
-- skimmed with it. It is the same failure mode as a guard with false positives: the check
-- survives, the attention it was supposed to buy does not.
--
-- JSON null here means "not measured", on either side:
--   * in the claim's context  — the measurement did not depend on this dimension
--   * in the caller's context — the current value could not be read (ORS down, say), which
--     is unknown, not changed
--
-- Prefer simply leaving the key out. The null handling is for callers that build the object
-- mechanically and cannot omit a key they have no value for.

CREATE OR REPLACE FUNCTION claim_stale(now_ctx jsonb)
RETURNS TABLE (claim_id bigint, claim_text text, dimension text,
               measured jsonb, current_value jsonb)
LANGUAGE sql STABLE AS $$
    SELECT c.id, c.text, k, c.context -> k, now_ctx -> k
    FROM claim c, jsonb_object_keys(c.context) AS k
    WHERE c.status IN ('open', 'survived')
      AND now_ctx ? k
      AND jsonb_typeof(c.context -> k) <> 'null'
      AND jsonb_typeof(now_ctx -> k) <> 'null'
      AND c.context -> k IS DISTINCT FROM now_ctx -> k
    ORDER BY c.id, k
$$;
