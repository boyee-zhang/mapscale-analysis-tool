-- A seventh status: a period that was DERIVED from prose rather than read off a labelled field.
--
-- 0010's six statuses were written when one template was in scope. Amsterdam publishes a
-- labelled APV block (`Datum van:`, `Bezoekers drukste moment:`), and `ok` means one of those
-- fields was present. After the national import there are 294 municipalities and almost none
-- of them use labels: 50 of 50 national bodies came back `no_period` while plainly containing
-- the event date in a sentence (docs/pg-notes/event-sources.md §7).
--
-- `scripts/event_dates.py` recovers those. It is measured, on 30 municipalities used in
-- neither development nor tuning: precision 100.0%, recall 65.5%, hallucination 0.0%
-- (docs/pg-notes/event-dates.md). Good, and NOT the same kind of fact as a labelled field —
-- one is a value the publisher wrote in a slot, the other is a value we inferred from a
-- sentence and could in principle infer wrongly.
--
-- So it gets its own status rather than being folded into `ok`. That is the same rule the six
-- existing statuses already follow: `no_period` is kept apart from `empty_body` because "the
-- document says nothing" and "there was no document" must not share a branch. Here, "the
-- publisher stated the date" and "we read the date out of a sentence" must not either —
-- a consumer that wants only publisher-stated values can still ask for exactly those, and
-- when a date turns out to be wrong, the status says in which of the two ways it was obtained
-- before anyone has to go looking.
--
--   ok_derived    body parsed, no labelled period field, but an event date was extracted
--                 from prose by the three-layer extractor

ALTER TABLE bekendmaking_body DROP CONSTRAINT bekendmaking_body_status_check;

ALTER TABLE bekendmaking_body ADD CONSTRAINT bekendmaking_body_status_check
    CHECK (status IN ('ok', 'ok_derived', 'no_period', 'empty_body', 'no_body_element',
                      'fetch_error', 'parse_error'));

-- Which rule produced a derived date, so a wrong one is traceable to one pattern instead of
-- to "the extractor". NULL for every status other than ok_derived. Deliberately free text and
-- not an enum: the cue vocabulary is expected to grow as municipalities are met, and a new
-- cue should be a row here, never a migration.
ALTER TABLE bekendmaking_body ADD COLUMN period_basis text;

COMMENT ON COLUMN bekendmaking_body.period_basis IS
    'For status=ok_derived: the cue that identified the date, e.g. label:datum evenement.';
