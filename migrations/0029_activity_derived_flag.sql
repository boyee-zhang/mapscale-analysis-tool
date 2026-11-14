-- Tell "derived to an empty string" apart from "not derived yet".
--
-- 0027 made the read view `coalesce(t.activity_text, '')` so that an undelivered row could not
-- silently vanish from search results. That was the right instinct and it collided with 0015's:
-- `''` is ALSO the value 0015 deliberately produced for a publication with no body and no title,
-- chosen over NULL "so a consumer sees the degraded value instead of silently losing the row".
-- After 0027 one value meant two things, and the falsifier's verdict 15 on ledger claim 18
-- demonstrated it on a freshly imported row:
--
--     read view    | ''                                                | {}
--     derive view  | 'Objectvergunning | het plaatsen van een bouwkraan' | {apv_regel,omschrijving}
--
-- A consumer could not tell that apart from a publication that genuinely has nothing to say.
--
-- `activity_derived` is that bit, and it is added rather than changing `activity_text`, because
-- making the column NULL for undelivered rows would break every caller that treats it as text
-- and would re-open exactly the hole 0027's coalesce closed. Appended last so the column order
-- 0015 and 0027 shipped is unchanged for anything doing `SELECT *`.
--
--   activity_derived = true,  activity_text = ''   -> no body and no title. 0015's sentinel.
--   activity_derived = false                       -> scripts/derive_activity.py has not run.
--
-- The honest reading of the second case is "unknown", not "empty", and `/search/notices`
-- already has the vocabulary for that: `shape: "unavailable"` with a reason, never `total: 0`.

CREATE OR REPLACE VIEW bekendmaking_activity AS
SELECT b.identifier, b.gemeente, b.doc_type, b.published_at,
       bb.visitors_peak, bb.status AS body_status,
       coalesce(t.activity_src, ARRAY[]::text[]) AS activity_src,
       coalesce(t.activity_text, '')             AS activity_text,
       (t.identifier IS NOT NULL)                AS activity_derived
FROM bekendmaking b
LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
LEFT JOIN bekendmaking_activity_text t ON t.identifier = b.identifier;

COMMENT ON VIEW bekendmaking_activity IS
    'What is actually being done, per publication. A lookup since 0027 -- the extraction runs '
    'in the bekendmaking_activity_derive view, driven by scripts/activity_rules.py. '
    'activity_derived (0029) separates 0015''s empty-string sentinel from "not derived yet".';
