-- What the publication DOCUMENT says, as opposed to what the SRU index says about it.
--
-- Why a second table rather than columns on `bekendmaking`: two sources, two importers, two
-- lifecycles. `bekendmaking` is the SRU index record and `import_bekendmaking.py` owns every
-- column in it. These fields come from fetching the document itself, one request per
-- publication, which fails and retries independently. Folding them in would put a column with
-- its own fetch state inside a table whose upsert asserts "the whole row is what SRU returned".
--
-- The driver, measured 2026-11-02 (docs/pg-notes/noise-groundtruth.md): the index record
-- averages 172 characters and its longest `abstract` in 61,853 rows is 281. For the APV family
-- the index keeps the permit type and the period's START date and drops the END date, so a
-- two-day skip container and 144 days of scaffolding with a crane are the same row. 51-54% of
-- any geographic slice was therefore undeterminable for the question "is this noisy", at every
-- radius tried -- a coverage gap, not a ranking or a semantics problem. No retrieval method
-- recovers a field that was never ingested.
--
-- Source endpoint is `zoek.officielebekendmakingen.nl/<identifier>.xml`, NOT the .html page.
-- Measured on the same document: 5,225 bytes of structured XML against 27,668 of HTML, and
-- 2 of 30 HTML bodies could not be extracted at all by a marker-based scraper while both of
-- those parse cleanly from XML. The 6.7% was the scraper, not the data.


CREATE TABLE bekendmaking_body (
    identifier text PRIMARY KEY REFERENCES bekendmaking (identifier) ON DELETE CASCADE,

    -- Why six states and not a boolean: the states a ground truth must keep apart are
    -- "the document says there is no period" and "we do not know what the document says".
    -- Collapsing them makes an un-fetched row look quiet, which is the empty-200 failure in
    -- docs/debugging-lessons.md -- "no more rows" and "the server gave up" must not share a
    -- branch. Absence of a row here is a seventh state, never attempted, and is deliberately
    -- not encoded as a status: a NOT NULL status means every row present was really attempted.
    --   ok              body parsed, at least one period field found
    --   no_period       body parsed and non-empty, no period field in it (the normal case
    --                   for omgevingsvergunning -- legitimate, not a failure)
    --   empty_body      body element present but contained no text
    --   no_body_element the XML carried no body at all
    --   fetch_error     HTTP did not yield a document
    --   parse_error     document fetched but would not parse
    status text NOT NULL CHECK (status IN (
        'ok', 'no_period', 'empty_body', 'no_body_element', 'fetch_error', 'parse_error')),

    -- All six period columns are NULL unless the document stated the value. Nothing here is
    -- defaulted or inferred, because the consumer is a ground truth: a fabricated end date
    -- would be indistinguishable from a real one and would silently become the measurement.
    start_date date,
    end_date   date,
    start_time time,
    end_time   time,

    -- Deliberately NOT stored, because the source never states them and both are exact
    -- functions of the columns above:
    --   all-day             start_date IS NOT NULL AND start_time IS NULL
    --   runs past midnight  end_time < start_time
    -- Storing either would assert something the publication does not say. Times are kept
    -- exactly as published, so 14.00-23.59 stays 14:00-23:59 and is not normalised to a day.
    --
    -- A publisher can encode a midnight crossing two ways and both are in the data, so the
    -- duration is (end_date + end_time) - (start_date + start_time) and the SIGN is the test:
    --   positive  the end date was advanced, as in 20-06 12:00 -> 21-06 01:00 (13 h, a real
    --             4,990-visitor event) -- unambiguous, use it as is
    --   negative  the end date was not advanced though the clock went backwards, so the
    --             publisher meant the next day; add a day rather than storing one
    -- An earlier version of this comment gave only `end_time < start_time AND end_date =
    -- start_date`, which is the second case alone and silently misses the first.

    -- The lines the four columns above were parsed from, verbatim and joined by ' | '. This is
    -- the only way to tell a parser bug from a source oddity after the fact, and the formats
    -- really do vary within one municipality: 'Tijd van: 09:00' and 'Tijd van: 14.00' both
    -- occur in Amsterdam evenement publications.
    period_raw text,

    -- Evenement publications carry 'Bezoekers drukste moment', the crowd at the busiest
    -- moment. It is the only quantitative intensity figure anywhere in this corpus and it
    -- bears directly on disturbance, so it is promoted rather than left in the text.
    visitors_peak integer CHECK (visitors_peak >= 0),

    -- The concatenated <al> (alinea) text of the document, as fetched. NOT de-boilerplated:
    -- an APV body is 91.3% byte-identical boilerplate (n=9) and stripping it is a sampled
    -- heuristic, so it stays an analysis step over raw input rather than something baked into
    -- the stored row. Keeping it also means the free-text experiments need no second fetch.
    body_text text,

    fetched_at timestamptz NOT NULL DEFAULT now(),
    etl_run    bigint NOT NULL REFERENCES etl_run (id)
);

-- The retry query is "which rows still need attention", i.e. a status filter, and the three
-- failure states are a small minority -- so a partial index over exactly them, not a btree
-- over a column that is ~96% two values. Same reasoning as etl_run's partial unique index.
CREATE INDEX bekendmaking_body_retry ON bekendmaking_body (status)
    WHERE status IN ('fetch_error', 'parse_error', 'empty_body');

-- The load-bearing analytical query is "periods overlapping this window", which needs both
-- ends; rows without a period are the majority and are excluded from the index entirely.
CREATE INDEX bekendmaking_body_period ON bekendmaking_body (start_date, end_date)
    WHERE start_date IS NOT NULL;
