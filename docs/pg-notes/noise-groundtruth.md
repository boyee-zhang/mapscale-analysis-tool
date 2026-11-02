# A ground truth for "will this be noisy", and what it says about embedding

Companion to `semsearch-ab.md`. That note ended on a blocking fact: n=15 queries can only
detect effects of 25 points or more, so an embedding experiment scoped as "15 resident
questions" cannot reach a verdict. The fix was to change the unit of measurement — pick one
concept, build a near-exhaustive ground truth for it, and count documents instead of queries.

The concept chosen is **noise disturbance**, with the requirement stated explicitly: not
"the document contains the word noise", but "a resident at this address would be disturbed".
That includes work whose *hours* are unreasonable even when no word for noise appears.

Everything below is measured. Where a number is from a sample, the n is given.

## The first measurement killed the obvious plan

`bekendmaking` holds no document text.

| | |
|---|---|
| rows | 61,853 |
| `abstract` non-empty | 58,532 |
| mean length of `title` + `abstract` | **172 characters** |
| longest `abstract` in the corpus | **281 characters** |
| rows whose text contains a clock time (`HH:MM`) | **359 = 0.58%** |
| rows whose text contains `Looptijd` | **0** |
| `raw` jsonb | metadata only — publication number, dates, publisher. No body. |

So the time-of-day requirement cannot be evaluated against the database as it stands. The
full document is reachable at `url` (`zoek.officielebekendmakingen.nl/<id>.html`, HTTP 200,
20–28 KB). A stratified sample of **30** documents was fetched, 3 per class across 10 classes,
and the body extracted between the markers `Informatie Printen Delen` and `Naar boven
Permanente link`.

**28 of 30 extracted; 2 returned an empty body** (`gmb-2026-403553` a verkeersbesluit,
`gmb-2026-283118`). A 6.7% failure rate on a marker-based extractor. Not fixed — if the
fetch is ever done at scale, those layouts need handling or explicit exclusion, because an
empty body and a body with no noise signal would otherwise land in the same bucket. This is
the same shape as the empty-200 lesson in `debugging-lessons.md`.

## Boilerplate share splits the corpus in two

Per family, the character share that is byte-identical across every document in that family
(longest common blocks ≥40 chars, intersected pairwise):

| family | n | boilerplate | unique chars | what the unique part is |
|---|---|---|---|---|
| APV — TVM / Object / Nachtwerk | 9 | **91.3%** | 286–306 | seven structured fields, nothing else |
| Amsterdam "besluit" template | 2 | **94.5%** | 142–143 | address + case number |
| maatwerkvoorschrift | 3 | 78.3% | 419–787 | free-text reason |
| sloop | 3 | 0% | 465–1,437 | free-text `Omschrijving:` |
| splitsing | 3 | 0% | 1,258–1,692 | free-text |
| evenement | 3 | 0% | 570–2,565 | structured, incl. `Tijd van/tot` |
| heien / damwand | 3 | 0% | 799–1,476 | free-text |

The nine APV bodies are 3,348–3,368 characters long. That near-constant length across three
*different permit types* is the whole finding: the variable part is 9% of the document and
reads

```
Besluit apv vergunning <status> - <street> ter hoogte van nummer: <n> in <city>
Stadsdeel: <district>
<PERMIT_TYPE>, Locatie: <street> ter hoogte van nummer: <n> in <city>
Looptijd : <start> t/m <end>
Verzonden naar aanvrager op: <date>
Kenmerk gemeente: Z/26/<id>
```

Every field is a date, an address, or a value from a closed list. By the test set out
earlier in this work — *can you write down the complete set of target values in advance?* —
this is a parsing problem. And the closed list is printed **inside the document itself**, in
the glossary paragraph that is part of the 91.3%: TVM, RVV-verkeersontheffing, Object,
Object+TVM, Winkeltijdenontheffing, Nachtwerkontheffing.

### The glossary paragraph poisons full-text noise search

That same boilerplate contains:

> `Nachtwerkontheffing: Het wordt toegestaan onder voorwaarden in de avonduren of het
> weekeinde (door) te werken en een zekere mate van lawaai te genereren, maar nooit meer
> dan 70Db.`

It appears in **every** APV publication whatever the permit actually is. `geluid|lawaai`
matched **9 of 9** sampled APV bodies, including the skip-container and traffic-measure ones.
So in the full text, `lawaai` has **zero discriminative power across ~22,000 APV rows**
(TVM 14,205 + Object 11,110, overlapping).

The 661-row noise vocabulary in `semsearch-ab.md` was counted on `abstract`, which does not
carry the glossary. **That number stands.** It would have been wrong by two orders of
magnitude had the corpus been the full text.

## Which fields survive into the database, and which do not

Checked by reading the fetched body next to the stored row for 10 documents.

| needed for the noise question | in the DB? |
|---|---|
| free-text work description (omgevingsvergunning) | **yes** — it *is* `abstract` |
| APV permit type (Object / Nachtwerk / TVM) | **yes** — `abstract` prefix |
| APV **duration** (end date) | **no** — only the start date survives |
| evenement **time of day** | **no** — dropped entirely |
| maatwerkvoorschrift reason | **yes, in `title`**; `abstract` is unusable |

Two consequences.

**The embedding experiment needs no fetching.** The free-text family — 14,130 rows with
`activiteit = bouwen`, plus sloop and heien — already has its applicant-written description
stored as `abstract`.

**The duration and time-of-day extension does need fetching**, and only for APV (~22,000) and
evenement (2,103). Observed durations in the 9 APV samples: 1, 1, 2, 3, 14, 21, 25, 144 days
(`20-07-2026 t/m 11-12-2026`). A 144-day object permit and a 1-day TVM are not the same event.
Observed evenement windows: `09:00–19:00`, `10.00–17:00`, `14.00–23.59`, alongside a
`Bezoekers drukste moment` field.

### Correction to an earlier measurement of my own

An earlier probe of work-date extraction over these rows reported "exactly one date per row,
no ranges" and treated that as a property of the source. It is not. The source carries
`Looptijd : <start> t/m <end>`; **our importer keeps only the start.** The measurement was of
the pipeline and was attributed to the world. Generalises: before calling an absence a
property of the data, check whether your own ingest created it.

## Where the noise question is actually open

Three sampled rows, all `maatwerkvoorschrift`, all matched by a keyword search for it:

| title (translated) | noise? | contains a noise word? |
|---|---|---|
| maatwerkvoorschriften **for permission to deviate from the hours of execution** of works | **yes** | **no** — it says `tijdstippen van uitvoering` |
| maatwerkvoorschriften for deviating from the applicable **noise provisions**, amplified music | yes | yes |
| maatwerkvoorschriften for temporarily **discharging groundwater** into the sewer | **no** | no |

One keyword retrieves all three. Only the free-text reason separates them, and the row that
most exactly matches the stated requirement — unreasonable working hours — carries no noise
vocabulary at all. That pair (a true positive with no keyword, a false positive sharing the
keyword) is the precision/recall failure embedding is supposed to repair.

So the corpus has two populations and they call for opposite methods:

- **APV family, ~22,000 rows (36%).** 91.3% constant text; 9% closed-value fields. Embedding
  cannot contribute and this is provable rather than a guess: any embedding of the full text
  is dominated by a 3,064-character constant. A regex over seven fields plus a lookup on
  permit type plus a threshold on duration is exact.
- **Free-text families.** `Omschrijving:` / `Toelichting:` on omgevingsvergunning, and the
  maatwerkvoorschrift reason clause. Applicant-written, open value space, the concept often
  not lexically present. This is the only place an embedding claim can be tested honestly.

## The slice to label

A ground truth has to be **exhaustive over a defined sub-population**, or recall is not
computable and the pooling bias from `semsearch-ab.md` returns. Narrowing by geography and
time does that; narrowing by concept does not, because the documents the concept misses are
exactly the ones under test.

Documents within radius *r* of `4.892, 52.373` (central Amsterdam), by window:

| radius | 1 month | 3 months | all 2026 |
|---|---|---|---|
| 400 m | 99 | 352 | **1,117** |
| 800 m | 353 | 1,369 | 4,156 |
| 1,500 m | 952 | 3,710 | 11,223 |

400 m / all 2026 = **1,117** is the proposed frame: 400 m is the order of magnitude of the
product's walking isochrone, so the slice *is* the production query; a full year avoids a
seasonal artefact; and 1,117 rows can be labelled completely.

Label the **patterns**, not the rows — the same discipline as `poi_category_rule`, where a
rule table maps `(osm_key, osm_value) -> category` because one concept is spread over
unrelated keys. A noise rule table maps `(family, permit_type or text pattern) -> impact`,
every row of the frame gets a label by rule, and only the residue is judged by hand. That
keeps the labelling auditable and re-runnable, which a per-row judgement is not.

## Status

Nothing is labelled yet. What is settled is the design and its cost: the free-text experiment
needs no fetching, the time dimension needs ~24,000 fetches restricted to two families, and
the APV family has a provable negative result for embedding that cost 30 HTTP requests.
