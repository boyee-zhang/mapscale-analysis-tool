-- A ground truth for "is the permitted activity loud", and the frame it is exhaustive over.
--
-- Why a frozen frame table instead of a radius in a WHERE clause.
--   The claim this benchmark rests on is that the labels are EXHAUSTIVE over a defined
--   sub-population -- without that, every unlabelled relevant document silently becomes a
--   false positive for whichever method finds it, which is pooling bias. If the frame were
--   the query `ST_DWithin(..., 300)`, the next `import_bekendmaking.py` run would add rows
--   inside that radius, the denominator would change under the labels, and the set would stop
--   being exhaustive with nothing in the schema noticing. So membership is materialised once,
--   as rows, and `noise_frame_unlabelled` below is the check that it still holds.
--
-- Why two disjoint geographies rather than one bigger radius.
--   Measured: a 300 m disc on central Amsterdam holds 587 publications, one on the Utrecht
--   binnenstad 367. Both are old dense centres, but a method tuned to canal-belt renovation
--   vocabulary would score well on Amsterdam alone and the benchmark could not tell. Keeping
--   the blocks named and separate means any result can be reported per block, and a gap
--   between them is a finding rather than an invisible bias.

CREATE TABLE noise_frame (
    name      text PRIMARY KEY,
    lng       double precision NOT NULL,
    lat       double precision NOT NULL,
    radius_m  integer NOT NULL CHECK (radius_m > 0),
    -- The frame is defined by geography and time ONLY, never by the concept being labelled.
    -- A frame narrowed by "documents that look noisy" would make recall unmeasurable.
    note      text
);

CREATE TABLE noise_frame_member (
    frame      text NOT NULL REFERENCES noise_frame (name) ON DELETE CASCADE,
    identifier text NOT NULL REFERENCES bekendmaking (identifier) ON DELETE CASCADE,
    PRIMARY KEY (frame, identifier)
);

CREATE INDEX noise_frame_member_ident ON noise_frame_member (identifier);

CREATE TABLE noise_label (
    identifier text PRIMARY KEY REFERENCES bekendmaking (identifier) ON DELETE CASCADE,

    -- Four values, not a boolean. The physical range runs from demolishing a building to
    -- installing an air conditioner, so a binary label would have to put the cut somewhere in
    -- the middle and a reader of the benchmark could not see where. `borderline` holds that
    -- judgement visibly; the primary metric scores `noisy` against `quiet` and reports
    -- `borderline` separately.
    --
    -- `unjudgeable` is never collapsed into `quiet`. A document whose text names no activity
    -- has no known answer, and scoring it as a true negative would reward every method for
    -- failing to retrieve it.
    label text NOT NULL CHECK (label IN ('noisy', 'borderline', 'quiet', 'unjudgeable')),

    -- The physical noise source in the source's own words ('sloop', 'versterkte muziek'), or
    -- 'n/a'. Names WHY, so a disagreement can be about this row rather than about the scheme.
    basis text NOT NULL,

    -- The substring that decided the label, so a spot audit need not re-read the document.
    evidence text,

    -- 'rule:<pattern>' when a reviewed pattern assigned it, 'manual' when read individually.
    -- Kept because the two have different error modes: a wrong rule is wrong in bulk.
    labelled_by text NOT NULL,

    labelled_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX noise_label_label ON noise_label (label);

-- Exhaustiveness as a query, not a promise. Must return zero rows before any metric is
-- computed from this ground truth.
CREATE VIEW noise_frame_unlabelled AS
SELECT m.frame, m.identifier, b.doc_type, b.title
FROM noise_frame_member m
JOIN bekendmaking b USING (identifier)
WHERE NOT EXISTS (SELECT 1 FROM noise_label nl WHERE nl.identifier = m.identifier);
