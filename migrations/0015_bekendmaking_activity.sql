-- The one field every retrieval method should index: what is actually being done.
--
-- Why this exists at all. `bekendmaking.title` + `abstract` average 174 characters and for one
-- large class say nothing about the activity: an Amsterdam `Besluit omgevingsvergunning
-- reguliere procedure verleend Stadionkade 140 1076BT Amsterdam` names the procedure and the
-- address and no works. 43.1% of Amsterdam's 12,882 omgevingsvergunning rows carry no activity
-- word in the indexed text at all. The body does carry it -- the same publication's body opens
-- `Omschrijving: vergroten van de woning ... en het kappen van een magnolia` -- so the activity
-- is upstream and was simply never extracted.
--
-- Why a view and not a column. The extraction is a pure function of `body_text`, which is
-- already stored, so a column would need a backfill and a second copy of these patterns inside
-- the importer, and the two would drift the first time a pattern improved. A view is
-- re-derivable: fix a pattern, and every row is fixed. If the per-query regex cost ever
-- matters it can be materialised then, on measurement rather than in anticipation.
--
-- Why these six field names and no others. They are a census, not a guess. Over all 61,853
-- bodies, every line matching `^<Capitalised label>:` was counted; the descriptive ones are:
--     Omschrijving    15050 docs, mean 82 chars   Amsterdam omgevings-/andere vergunning
--     Toelichting      4628 docs, mean 50 chars   Utrecht omgevingsvergunning
--     Onderwerp        1742 docs, mean 74 chars   Amsterdam omgevingsmelding ("Melding graven bodem")
--     Activiteiten     1590 docs, mean 88 chars   Amsterdam evenement -- what happens, e.g. "live DJ-sets"
--     Betreft           915 docs, mean 73 chars   Utrecht verkeersbesluit
--     Naam evenement    479 docs, mean 24 chars   Utrecht evenement -- the name only
-- The high-frequency labels are boilerplate, not per-document data: `Object`, `TVM`,
-- `Nachtwerkontheffing` and `Winkeltijdenontheffing` each appear in 22,664 bodies because they
-- are the APV glossary, printed in full in every APV publication. That is the same boilerplate
-- dominance measured earlier at 91.3% of an APV body, and it is why `body_text` itself is a
-- poor thing to index and this view exists.
--
-- Why a concatenation rather than one field. A publication can carry several of these at once
-- (a Utrecht event has both `Naam evenement` and, in Amsterdam's template, `Activiteiten`), and
-- each says something the other does not. Joining every present field in a fixed order gives
-- the richest honest description, and `activity_src` records which ones contributed so a bad
-- extraction is traceable to one field instead of to "the view".
--
-- The capture group is `[^\n]+`, NOT `.+`. In Postgres regex `.` matches a newline unless the
-- `n` flag is set, so `Omschrijving\s*:\s*(.+)` captures the field AND the whole rest of the
-- document -- measured here as a mean `activity_text` of 1208 characters where the field itself
-- is about 82, i.e. the boilerplate-swamps-the-signal failure all over again, self-inflicted.
--
-- Addresses are stripped from the Amsterdam APV line (`ter hoogte van ...`, `, Locatie: ...`).
-- Street names in indexed text cause false positives -- a search for `bouwen` matches
-- `Ewoutszstraat` -- and the address is already in `bekendmaking_locatie`. This only cleans
-- this one field; `title` and `abstract` still carry addresses.

CREATE VIEW bekendmaking_activity AS
WITH f AS (
    SELECT b.identifier, b.gemeente, b.doc_type, b.published_at, b.title,
           bb.visitors_peak, bb.body_text, bb.status AS body_status,
           (regexp_match(bb.body_text, '(?:^|\n)Omschrijving\s*:\s*([^\n]+)'))[1]   AS omschrijving,
           (regexp_match(bb.body_text, '(?:^|\n)Toelichting\s*:\s*([^\n]+)'))[1]    AS toelichting,
           (regexp_match(bb.body_text, '(?:^|\n)Onderwerp\s*:\s*([^\n]+)'))[1]      AS onderwerp,
           (regexp_match(bb.body_text, '(?:^|\n)Activiteiten\s*:\s*([^\n]+)'))[1]   AS activiteiten,
           (regexp_match(bb.body_text, '(?:^|\n)Betreft\s*:\s*([^\n]+)'))[1]        AS betreft,
           (regexp_match(bb.body_text, '(?:^|\n)Naam evenement\s*:\s*([^\n]+)'))[1] AS naam_evenement,
           -- Amsterdam APV/exploitatie: line 1 is `Stadsdeel: X`, line 2 carries the permit
           -- subtype (a closed vocabulary: Objectvergunning / TVM / Nachtwerkontheffing /
           -- Filmmelding / RVV) or, for events and hospitality, a one-line summary.
           CASE WHEN bb.body_text ~ '^Stadsdeel\s*:' THEN
               btrim(regexp_replace(
                   split_part(split_part(bb.body_text, E'\n', 2), ',', 1),
                   '\s+(ter hoogte van|op het adres|op adres)\s.*$', ''))
           END AS apv_regel
    FROM bekendmaking b
    LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
)
SELECT f.identifier, f.gemeente, f.doc_type, f.published_at, f.visitors_peak, f.body_status,
       -- Which fields answered, in the order they were concatenated. Empty array means the
       -- fallback was used, and `activity_text` is then the first body line or the title.
       array_remove(ARRAY[
           CASE WHEN f.apv_regel      IS NOT NULL THEN 'apv_regel'      END,
           CASE WHEN f.omschrijving   IS NOT NULL THEN 'omschrijving'   END,
           CASE WHEN f.toelichting    IS NOT NULL THEN 'toelichting'    END,
           CASE WHEN f.onderwerp      IS NOT NULL THEN 'onderwerp'      END,
           CASE WHEN f.naam_evenement IS NOT NULL THEN 'naam_evenement' END,
           CASE WHEN f.activiteiten   IS NOT NULL THEN 'activiteiten'   END,
           CASE WHEN f.betreft        IS NOT NULL THEN 'betreft'        END
       ], NULL) AS activity_src,
       coalesce(
           nullif(concat_ws(' | ', f.apv_regel, f.omschrijving, f.toelichting, f.onderwerp,
                                   f.naam_evenement, f.activiteiten, f.betreft), ''),
           -- No labelled field. Amsterdam's older omgevingsvergunning template is free prose
           -- and its first line holds the activity ("het inzetten van een mobiele hijskraan
           -- voor ... renovatie van het Paleis"), so it is worth having -- but that same first
           -- line is sometimes pure boilerplate shared across unrelated publications. Measured:
           -- 8 frame rows all open `Het college van burgemeester en wethouders van Amsterdam,`
           -- while their titles separate a disabled-parking-bay order from
           -- `Mededeling opruimen gezonken vaartuig` -- salvaging a sunk vessel from a canal,
           -- which is crane work. Taking the body line alone made those 8 indistinguishable and
           -- threw away the only text that told them apart, so the title goes in front of it.
           nullif(btrim(concat_ws(' | ', f.title,
                                  nullif(btrim(split_part(f.body_text, E'\n', 1)), ''))), ''),
           -- No body and no title: deliberately empty string, not null, so a consumer sees the
           -- degraded value instead of silently losing the row.
           ''
       ) AS activity_text
FROM f;
