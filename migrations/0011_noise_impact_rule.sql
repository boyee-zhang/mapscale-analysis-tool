-- Which publications would disturb a resident, as a rule table rather than a query.
--
-- Modelled on poi_category_rule, for the same reason given there: one concept is spread over
-- unrelated fields. "Noisy" is signalled by the abstract's wording for the APV family, by
-- `doc_type` for evenement, and by `activiteit` for building work -- and the first version of
-- these rules lived as ad-hoc SQL that read `title || abstract` only. It therefore missed 24
-- evenementenvergunning rows in a 1,117-row frame whose `doc_type` carried the answer while
-- their text did not. A regex gains a field; a table makes the field a first-class match
-- target so the omission cannot recur, and makes the candidate set of a benchmark
-- reproducible and auditable instead of retyped.
--
-- The labels are patterns, not rows: 61,853 publications cannot be judged individually, and a
-- per-row judgement cannot be re-run after a rule changes. See docs/pg-notes/noise-groundtruth.md.

CREATE TABLE noise_impact (
    impact   text PRIMARY KEY,
    label_en text NOT NULL,
    -- Display and aggregation order. Separate from rule priority: this orders the CLASSES,
    -- rule priority decides which class a given publication lands in.
    rank     smallint NOT NULL
);

CREATE TABLE noise_impact_rule (
    -- Which field the pattern is applied to. The whole point of the table.
    --   text        case-insensitive regex over title || ' ' || abstract
    --   doc_type    case-insensitive regex over doc_type
    --   activiteit  exact membership in the activiteit array (a closed 9-value vocabulary,
    --               so a regex would buy nothing and could match across elements)
    match_on text NOT NULL CHECK (match_on IN ('text', 'doc_type', 'activiteit')),
    pattern  text NOT NULL,
    impact   text NOT NULL REFERENCES noise_impact (impact),
    -- Lowest wins, as in poi_category_rule. A publication can match several rules: a
    -- nachtwerkontheffing is also an APV object permit, and the explicit signal must win.
    priority smallint NOT NULL DEFAULT 100,
    note     text,
    PRIMARY KEY (match_on, pattern)
);

CREATE INDEX noise_impact_rule_impact ON noise_impact_rule (impact);


-- The labelled candidate set. A view, not a materialised table: the rules are expected to
-- change while the ground truth is built, and a stale copy of a classification is the one
-- thing that would quietly invalidate a benchmark.
--
-- `bekendmaking_body` is joined in rather than folded into the impact, because the two answer
-- different questions and must not be confused. A rule says what KIND of thing this is; the
-- period says HOW LONG and WHEN. Resolving 'undeterminable' needs both, and the caller can
-- see which of the two is missing -- a body row absent entirely (never fetched) reads
-- differently from body_status = 'no_period' (fetched, says nothing).
CREATE VIEW bekendmaking_noise AS
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
       -- NULL unless both ends are known. Inclusive of both days, so a single-day permit is
       -- 1, which is what "how many days of works" means to a resident.
       CASE WHEN bb.start_date IS NOT NULL AND bb.end_date IS NOT NULL
            THEN (bb.end_date - bb.start_date) + 1 END AS days,
       -- Crossing midnight is end_time < start_time whichever way the publisher encoded the
       -- end date, so the signed interval is the honest answer: positive means the end date
       -- was advanced and can be used as published, negative means it was not.
       CASE WHEN bb.start_date IS NOT NULL AND bb.start_time IS NOT NULL
                 AND bb.end_date IS NOT NULL AND bb.end_time IS NOT NULL
            THEN (bb.end_date + bb.end_time) - (bb.start_date + bb.start_time) END AS span
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
    -- pattern breaks the tie so the label is deterministic when two rules share a priority.
    ORDER BY ru.priority, ru.pattern
    LIMIT 1
) r ON true;
