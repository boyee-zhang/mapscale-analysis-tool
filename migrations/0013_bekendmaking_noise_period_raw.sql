-- Expose period_raw through the view.
--
-- 0011 kept it in the table and left it out of the view, which defeats the reason it is
-- stored: it is the only way to tell a parser bug from a source defect, and the view is what
-- anything actually reads. Both oddities the first full backfill turned up were settled from
-- it in one query --
--   2 rows (0.008% of 21,802 with a duration) have the end BEFORE the start, and
--   `Looptijd :30-08-2026 t/m 12-08-2026` shows the publication itself says so;
--   the longest, 1,097 days, is `Looptijd :17-09-2026 t/m 17-09-2029`, a real three-year
--   permit rather than a misread century.
-- Without period_raw in the view both would have needed a join to disambiguate, and the
-- cheaper guess -- "the parser is broken" -- would have been the tempting one.
--
-- `days` and `span` stay SIGNED. Nulling a negative duration would hide a source defect
-- behind the same NULL that means "the document does not say", which is the distinction this
-- whole table exists to preserve. A consumer averaging `days` should filter `days >= 1`.

CREATE OR REPLACE VIEW bekendmaking_noise AS
SELECT b.identifier,
       b.doc_type,
       b.gemeente,
       b.published_at,
       coalesce(r.impact, 'unlabelled') AS impact,
       r.matched_on,
       r.pattern        AS matched_pattern,
       bb.status        AS body_status,
       bb.start_date,
       bb.end_date,
       bb.start_time,
       bb.end_time,
       bb.visitors_peak,
       CASE WHEN bb.start_date IS NOT NULL AND bb.end_date IS NOT NULL
            THEN (bb.end_date - bb.start_date) + 1 END AS days,
       CASE WHEN bb.start_date IS NOT NULL AND bb.start_time IS NOT NULL
                 AND bb.end_date IS NOT NULL AND bb.end_time IS NOT NULL
            THEN (bb.end_date + bb.end_time) - (bb.start_date + bb.start_time) END AS span,
       -- Appended last, not placed next to the columns it explains: CREATE OR REPLACE VIEW
       -- can only add columns at the end, and dropping the view to reorder them would break
       -- anything that had come to depend on it for the sake of cosmetics.
       bb.period_raw
FROM bekendmaking b
LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
LEFT JOIN LATERAL (
    SELECT ru.impact, ru.priority, ru.pattern, ru.match_on AS matched_on
    FROM noise_impact_rule ru
    WHERE CASE ru.match_on
              WHEN 'text' THEN
                  (coalesce(b.title, '') || ' ' || coalesce(b.abstract, '')) ~* ru.pattern
              WHEN 'doc_type' THEN b.doc_type ~* ru.pattern
              WHEN 'activiteit' THEN ru.pattern = ANY (b.activiteit)
          END
    ORDER BY ru.priority, ru.pattern
    LIMIT 1
) r ON true;
