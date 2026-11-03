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

## Feasibility of the frame, measured — and it inverts the priority

The tiered rules were run over the geographic frame at three radii, all of 2026.

| radius | total | T1 explicit noise | T2 loud activity named | T3 evenement | **T4 undeterminable** |
|---|---|---|---|---|---|
| 400 m | 1,117 | 26 | 7 | 56 | **594 (53%)** |
| 800 m | 4,156 | 66 | 15 | 225 | **2,135 (51%)** |
| 1,500 m | 11,223 | 194 | 59 | 448 | **6,026 (54%)** |

T4 is Object + TVM + `activiteit = bouwen` with no further detail. **Its share is stable at
51–54% across all three radii**, so it is a structural property of the corpus, not a sampling
artefact. For those rows the honest label is neither "noisy" nor "quiet" but *undeterminable
from the data we hold* — a skip container for two days and scaffolding with a crane for 144
days are the same row in this database, because the end date was never imported.

That is the third category from the parsing/semantics discussion: a **coverage** problem
wearing the costume of a semantic one. Forcing those 51% into yes/no would make the ground
truth fiction, and every later comparison would be measuring the guess rather than the method.

**So the binding constraint is not the retrieval method. It is a field our own importer
drops.** No embedding, LLM or `ts_rank_cd` fix can recover information that was never
ingested. The repair is a parsing change in `scripts/import_bekendmaking.py` plus a refetch
limited to the APV and evenement families (~24,000 documents): keep `Looptijd` *end* date,
and `Tijd van` / `Tijd tot` / `Bezoekers drukste moment` for evenement.

### Revised order of work

1. **Capture the dropped fields.** Converts ~51% of any slice from undeterminable to
   labellable. Everything below is blocked on it, because "near-exhaustive" is the entire
   point of the ground truth.
2. **Then build the ground truth at 1,500 m** — 11,223 rows, ~253 T1+T2 high-confidence
   positives. T1+T2 scales linearly with the frame (33 → 81 → 253), and 400 m yields only 33,
   which is too few to compare two retrieval methods on.
3. **Then test embedding on the free-text families only.** The APV negative result above does
   not transfer to them, and they are where the `tijdstippen van uitvoering` /
   `lozen van grondwater` pair lives.

### Rule gap, surfaced

At 400 m, 244 rows (21.8%) matched no rule: 150 `andere vergunning`, 46 `omgevingsvergunning`,
**24 `evenementenvergunning`**, 13 `andere beschikking`, 8 `omgevingsmelding`, 3 other. The 24
evenement rows were missed because the rules read `title || abstract` only — `doc_type` was
never consulted, though it carries the answer. The rule table must match on `doc_type` as well
as on text patterns. Not fixed; it changes the T3 count upward when it is.

`andere vergunning` being the largest unlabelled bucket is consistent with the earlier finding
that it is 53% of the whole corpus and carries no usable taxonomy.

---

# P0 built: the dropped fields are back, and the rules are a table

Three repairs, in the order their results depend on each other.

## A. `bekendmaking_body` — what the document says

Migration `0010`, importer `scripts/import_bekendmaking_body.py`. One row per publication whose
document was fetched, carrying `start_date` / `end_date` / `start_time` / `end_time` /
`period_raw` / `visitors_peak` / `body_text` and a `status`.

Source is **`<identifier>.xml`, not the `.html` page** — 5,225 bytes against 27,668 on the same
document, structured, and free of the navigation chrome that made the HTML scraper fail.

Two fields the brief asked for are **deliberately not stored**:

| asked for | why not | how to get it |
|---|---|---|
| all-day / time precision | the source never states it | `start_date IS NOT NULL AND start_time IS NULL` |
| crosses midnight | ditto, and it is derivable | `end_time < start_time` |

Both are exact functions of the stored columns. A stored copy could only ever disagree with
the source, and the brief's own second criterion is that nothing be defaulted.

`visitors_peak` was promoted instead. `Bezoekers drukste moment` is the **only quantitative
intensity figure anywhere in this corpus**, and for a disturbance question a 4,990-person
event is not a 25-person one.

### Six statuses, not four

`no_period` had to be added: a body that was read successfully and simply says nothing about a
period is the normal case for omgevingsvergunning, and it is not a failure. Collapsing it into
`empty_body` would make "the document says nothing" and "we failed to read the document" share
a branch — the empty-200 lesson in `debugging-lessons.md`. `EXTRACTION_FAILED` likewise splits
into `fetch_error` and `parse_error` because they retry differently.

Absence of a row is a seventh state — *never attempted*. It is deliberately **not** encoded as
a status, so `status NOT NULL` means every row present was really tried.

### Acceptance, measured on 60 evenement publications

| criterion | result |
|---|---|
| period stored when the document states it | 37/37 `ok` rows carry all four fields; 36/37 carry `visitors_peak` |
| NULL when it does not, with no defaults | 0 `no_period` rows carry any period value; 0 `ok` rows lack `period_raw` |
| re-run is idempotent | `INSERT 0, UPDATE 0, unchanged 60` — **and `n_tup_upd = 0`, `n_dead_tup = 0`** |
| an existing row updates when the source differs | broke one row, re-ran, `UPDATE 1`, values restored |

The third one is the point worth keeping. "Unchanged 60" is the importer's own report; the heap
counters are independent evidence that no tuple was rewritten. `fetched_at` is excluded from
the `IS DISTINCT FROM` comparison on purpose — it changes every run by definition, so including
it would rewrite every tuple and double the heap, the trap stage 1 hit on `poi`.

Richest row so far: `20-06-2026 12:00 → 21-06-2026 01:00`, **4,990 visitors**. Note the
publisher advanced the end *date* for the midnight crossing, so a rule of
`end_time < start_time AND end_date = start_date` would have missed it — an earlier version of
the migration comment said exactly that and was corrected. The honest test is the signed
interval `(end_date + end_time) - (start_date + start_time)`.

## B. The 6.7% was the scraper, not the data

Both HTML bodies a marker-based extractor could not read — `gmb-2026-403553` and
`gmb-2026-283118` — **parse cleanly from XML**, into 71 and 20 non-empty `<al>` elements.
Choosing the structured endpoint removed the failure mode rather than handling it.

The caution against extrapolating 6.7% from n=30 was right, and the resolution is better than
a corrected rate: no rate is claimed. The status taxonomy stays because a genuine failure path
exists and a ground truth must not confuse an unread document with a quiet one.

## C. `noise_impact_rule` — the rules as a table

Migrations `0011` (structure + view `bekendmaking_noise`) and `0012` (the rules).
`match_on ∈ (text, doc_type, activiteit)` makes the field a first-class match target instead of
a regex over one concatenation, so the gap that lost 24 rows cannot recur, and a benchmark's
candidate set is reproducible rather than retyped. Modelled on `poi_category_rule`: structure
and reference data in separate migrations, lowest `priority` wins.

Re-run over the 1,117-row frame against the old text-only rules:

| | |
|---|---|
| labelled by the old rules | 867 |
| labelled by the rule table | 903 |
| **newly labelled** | **36** |
| **lost** | **0** |

The 36 are exactly **24 `evenementenvergunning`** matched by `doc_type` — the predicted gap, to
the row — plus 6 `omgevingsvergunning` via `activiteit` (slopen/kappen) and 6 `verkeersbesluit`.

Revised distribution of the frame:

| impact | count | % |
|---|---|---|
| undeterminable | 565 | 50.6 |
| none | 219 | 19.6 |
| unlabelled | 214 | 19.2 |
| event | 79 | 7.1 |
| explicit | 26 | 2.3 |
| loud_activity | 14 | 1.3 |

`undeterminable` is still half the frame **as classified from the index record** — that is what
the rules alone can say. The view joins `bekendmaking_body` so the period resolves it; the
numbers for that come after the backfill.

## The backfill, and the coverage boundary it draws

`--family all`, 24,313 publications, **7 min 58 s** at concurrency 4 (`etl_run` 23).

| | |
|---|---|
| rows in `bekendmaking_body` | 24,373 |
| `ok` | 23,627 (97.0%) |
| `no_period` | 746 |
| `fetch_error` + `parse_error` + `empty_body` + `no_body_element` | **0** |
| date parse failures among `ok` rows | **0** |
| time parse failures among rows whose `period_raw` has `Tijd van`/`Tijd tot` | **0 of 1,579** |
| rows with `end_date` — the field the index dropped | **23,626** |
| rows with `start_time` | 1,579 |
| rows with `visitors_peak` | 1,443 |
| all-day (date, no clock) | 22,048 |
| `body_text` | 71 MB raw, 63 MB as table + indexes |

A phantom to record, because it was my own query and not the data: a first pass reported
22,040 time-parse failures. The predicate was `period_raw ~* 'Tijd'`, which matches
**`Looptijd`**. Measured against `Tijd van` the real figure is 0 of 1,579. A substring test on
a field label is not a test of the field.

### Two source defects, settled from `period_raw` in one query

- **2 rows (0.008% of 21,802 with a duration) have the end before the start.**
  `Looptijd :30-08-2026 t/m 12-08-2026` → −17 days. The publication itself says so; the parser
  is reading it correctly. Left as published, and `days` stays signed: nulling it would hide a
  source defect behind the same NULL that means "the document does not say".
- **The longest duration, 1,097 days, is real**: `Looptijd :17-09-2026 t/m 17-09-2029`, a
  three-year permit, not a misparsed century.

Both would have needed a join to diagnose had `period_raw` stayed out of the view, and the
cheap guess — "the parser is broken" — would have been the tempting one. Migration `0013`
appends it (`CREATE OR REPLACE VIEW` can only add columns at the end).

### The corrected midnight rule, now measured

**87 rows cross midnight. 85 have the end date advanced; 2 do not.** The rule this note first
wrote down — `end_time < start_time AND end_date = start_date` — would have found **2 of 87,
a recall of 2.3%**. The general test is `end_time < start_time`, and the sign of
`(end_date + end_time) - (start_date + start_time)` says which of the two encodings a
publisher used.

### Did it resolve `undeterminable`? Yes, by two thirds

Corpus-wide, 21,802 of 33,693 `undeterminable` rows now carry a duration (**64.7%**), and
`event` is at 69.2%. On the 1,117-row frame:

| impact | total | no duration | 1–2 d | 3–14 d | 15–60 d | >60 d |
|---|---|---|---|---|---|---|
| undeterminable | 565 | 180 | 123 | 78 | 117 | **67** |
| event | 79 | 3 | 68 | 3 | 5 | 0 |
| explicit | 26 | 14 | 11 | 1 | 0 | 0 |
| loud_activity | 14 | **14** | 0 | 0 | 0 | 0 |

385 of 565 now have a duration and they spread across the whole range. The class that was a
single undifferentiated 51% is now a one-day traffic measure (123), a fortnight of works (78),
two months of scaffolding (117) and 67 permits running longer than that. Corpus median is
7 days, p90 71, p99 236.

### The boundary: a period exists if and only if the permit is APV or evenement

`loud_activity` — 2,111 rows, and the class most directly about noise (felling, demolition,
pile driving) — has **no duration at all** in the frame. That is a property of the source, not
of the fetch: **0 of 12 sampled omgevingsvergunning / kapvergunning documents carry a period.**
They state what will be done and when the decision was sent, never when the work happens.

(18 `loud_activity` rows do have one, and they are not counter-examples: they fell inside the
APV scope because their text names both a loud activity and an object/TVM permit, so the
period is the APV permit's.)

Their free text carries **scale** instead — `kappen van 1 boom (wilg)` against
`kappen van 17 bomen verspreid over de openbare ruimte`, `snoeien van 3 houtopstanden` — which
is real semantic variation and exactly the open-value-space material the embedding question
needs. It is simply not timing.

So the stated concept — noise *including* unreasonable working hours — is fully evaluable on
the APV + evenement half and **not** on the other. The ground truth therefore needs a third
label, not a yes/no: *loud activity, timing unknown*. Collapsing that into either "noisy" or
"quiet" would put a guess into the measurement, which is the failure this entire narrowing was
undertaken to avoid.

# The concept, without time

Time leaves the concept. The reason is a property of the source, measured in the section
above: a period exists if and only if the permit is APV or evenement, and 0 of 12 sampled
`omgevingsvergunning`/`kapvergunning` documents carry one at any depth. A concept that needs
timing is therefore unanswerable on 53% of the corpus, and the ground truth would have had to
carry a third label — *loud activity, timing unknown* — for the largest and most noise-relevant
family. Dropping time makes the label binary and the corpus uniformly judgeable.

What this costs, stated plainly so no later claim overreaches it: the original scope said
unreasonable working hours are themselves noise ("执行的时间太过分了 也是有噪声的"). That is now
outside the concept. This ground truth cannot distinguish a three-day daytime demolition from a
sixty-day permit for night work. It measures **whether the permitted activity is loud**, not
whether it will disturb a particular person. For comparing retrieval methods that is enough,
because every method is scored against the same labels. For a product claim about disturbance it
is not, and the period columns built in the section above are what a later version would use.

## The question each label answers

> Does carrying out the activity this document permits or announces produce loud noise at its
> location?

Not: does the document contain a noise word. The two come apart in both directions, and both
directions were measured. A document can say `geluid` and permit nothing loud — the APV
boilerplate glossary defines `Nachtwerkontheffing` using `lawaai` and `70Db`, so a full-text
search for `geluid|lawaai` matches 9 of 9 sampled APV bodies, a term with zero discrimination.
A document can permit something unmistakably loud and contain no noise word at all — `het
kappen van een magnolia` plus `constructieve aanpassingen` (gmb-2026-287002), where neither the
title nor any noise vocabulary appears.

## Four labels, because a binary one hides the judgement

The physical range runs from demolishing a building to installing an air conditioner. A binary
label forces a cut somewhere in the middle, and a reader of the benchmark cannot see where it
was made or how much the result depends on it. So the stored label has four values and the
binary is derived from it, with the cut written down:

| label | the test it must pass | examples from the frame |
|---|---|---|
| `noisy` | the work touches **structure** (load-bearing walls, foundation, cellar, roof plane, floors), moves **ground or water**, **demolishes**, **fells a tree**, remakes a **whole building**, or produces **amplified sound, performance or a crowd** | funderingsherstel met schroefinjectie, doorbraken in dragende muren, werfkelderherstel, graven in de bodem, open bodemenergiesysteem, tramspoor vervangen, boomstobbe met een knijperauto, kantoor naar 16 appartementen, dakkapel (het dakvlak wordt opengezaagd), live DJ-sets, Brassband Ritmo, nachtwerkontheffing |
| `borderline` | work on the **skin or the fittings only** — facade, frames, glazing, signage, awnings, solar panels, air-conditioning, non-structural interior — or **occupying space for works** (scaffold, container, street closure), or **filming** | gevelreclame, kozijnen vervangen, airco-buitenunit, zonnepanelen, werksteiger van 16 meter, objectvergunning, TVM, filmmelding, brandwerend plafond |
| `quiet` | **no physical work at all**: a legal, administrative or paper act, or a change of use, or legalising something already standing | kadastraal splitsen, opkoopbescherming, exploitatievergunning, alcoholwetontheffing, vakantieverhuur, verkeersbesluit, legaliseren van een bestaande warmtepomp, gevelkleur wijzigen |
| `unjudgeable` | the text names **no activity**, so the question has no answer in the document | `het wijzigen van een eerder verleende vergunning` — which permit, and to do what, is not said |

The rule is written as a test rather than as a list of examples on purpose. An earlier draft of
this table put `dakkapel` under `borderline` next to `airco-unit`, and that drafting choice was
the only thing holding it there: building a dormer opens the roof plane, so the structural test
puts it with the roof work it resembles. Where an example and the test disagreed, the test won
and the example moved.

The primary metric uses `noisy` as the positive class and `quiet` as the negative, and reports
`borderline` separately. That is the standard treatment of partial relevance, and it means the
one genuinely subjective decision in this ground truth — where small works fall — changes a
number the reader can see rather than the labels underneath it.

`unjudgeable` is a label, never a silent `quiet`. This is the pollution the P0 brief warned
about: an empty or activity-free document scored as "contains no noise information" would be
counted as a true negative, and every method would be rewarded for failing to retrieve a
document whose answer nobody knows. Rows labelled `unjudgeable` are excluded from both classes
and their count is reported, because that count is the ground truth's own coverage.

Each label carries two further columns, for auditing rather than for scoring:
`basis` names the physical noise source in the source's own words (`sloop`, `versterkte muziek`,
`n/a`), and `evidence` holds the substring that decided it. A spot audit can then disagree with
a specific label without re-reading the document.

# The ground truth, built: 1,316 publications, labelled one at a time

> Grown from 954 to 1,316 on 2026-11-03 by adding a third block, **ams-osdorp-600**, to test
> whether the retrieval result generalises off the canal belt. The test and its outcome — a
> prediction of mine that failed — are in `noise-retrieval-fts.md`. Everything below about
> method applies unchanged to the new block; the figures quoted as "954" are the first two
> blocks, which is what the measurements in this file were taken on.

## The frame, and why it is rows rather than a radius

Three discs, each labelled exhaustively: **ams-centrum-300** (the canal belt, 587
publications), **utr-binnenstad-300** (around the Domtoren, 367) and **ams-osdorp-600**
(Osdorp, Nieuw-West, 362 — 600 m rather than 300 because the publication density out there is
about a fifth of the centre's). 1,316 in all, out of a corpus of 61,853 — 53,712 Amsterdam,
7,287 Utrecht, 848 Diemen and 6 strays, all of 2026.

Osdorp's distribution is **77 noisy / 169 borderline / 116 quiet / 0 unjudgeable**. It was
labelled with the same rule and, wherever a phrase already had one, with the precedent already
set in the first two blocks — `exploitatievergunning` quiet (99/99), `TVM` borderline (59/59),
`Nachtwerkontheffing` noisy (10/10), and so on — so that the blocks are comparable rather than
merely similar. Two families had no precedent and were decided from the rule itself: **tree
felling** (the rule names it under `noisy`) and **events**, for which the cut had to be written
down to be applied uniformly — music, amplified sound, a performance, fairground rides, a sports
tournament or an outdoor market with stalls is `noisy`; an outdoor gathering with none of those
is `borderline`; a static indoor display or a seated meal is `quiet`.

The frame is defined by geography and time only, never by the concept. A frame narrowed by
"documents that look noisy" would make recall unmeasurable, which is the whole reason for
labelling a small area completely instead of a large sample of likely hits.

Membership is **materialised as rows** in `noise_frame_member`, not left as a `ST_DWithin`
predicate. The claim the benchmark rests on is exhaustiveness: if the frame were a query, the
next `import_bekendmaking.py` run would add publications inside that radius, the denominator
would shift under the labels, and the set would quietly stop being exhaustive with nothing in
the schema noticing. `noise_frame_unlabelled` is the check, and it must return zero rows before
any metric is computed. It does.

Two blocks rather than one larger disc, because a method tuned to canal-belt renovation
vocabulary would score well on Amsterdam alone and the benchmark could not tell. Keeping the
blocks named and separate makes a gap between them a finding instead of an invisible bias — and
there already is one, see below.

## The labels were NOT produced by rules, and that is the point

The obvious way to label 954 documents is to write regular expressions and let them fly. That
would have invalidated the experiment. A regex over document text **is** a keyword retrieval
method, so a ground truth built from patterns would hand full marks to full-text search by
construction and measure every other method against a keyword-derived truth. The rule table in
`noise_impact_rule` (migrations 0011/0012) remains the right tool for *candidate generation*;
as ground truth it would have rigged the comparison.

So every one of the 954 rows carries `labelled_by = 'manual'`: each distinct `activity_text` was
read and judged. Deduplication made that affordable rather than approximate — 954 rows hold 560
distinct activity texts, of which 109 recur (503 rows) and 451 are unique. Labelling a repeated
text once and propagating it is not sampling: for those rows the text *is* the whole of what the
document says about its activity, so reading it once reads them all.

Where a term's meaning was set by the issuing authority, that definition was used instead of an
impression. The Amsterdam APV boilerplate defines its own permit subtypes, verbatim:

> `Nachtwerkontheffing`: Het wordt toegestaan onder voorwaarden in de avonduren of het weekeinde
> (door) te werken en **een zekere mate van lawaai te genereren, maar nooit meer dan 70Db**.
> `TVM`: ... parkeervakken zijn afgezet voor werkzaamheden of een gehele straat vanwege kraanwerk.
> `Object`: er wordt een steiger, container, puinbak of mobiel toilet geplaatst voor werkzaamheden.

`Nachtwerkontheffing` is therefore `noisy` by the municipality's own words, not by inference.
`TVM`, `RVV` and `Object` assert that works are happening and never say what they are, which is
why they are `borderline` and not a guess in either direction.

For events named but not described (Utrecht publishes `Naam evenement` alone), the policy was:
`noisy` only when the **name itself denotes a noise source** — parade, concert, aubade,
dansparade, "Utrecht Blaast Buiten" — and `borderline` otherwise, however likely outside
knowledge might make it. `UIT-Week Janskerkhof` is `borderline` for that reason even though it
is in fact a stage festival: the document does not say so, and a label that needs knowledge the
document lacks is not a label a retrieval method can be scored against.

## What is in it

| | ams-centrum-300 | utr-binnenstad-300 |
|---|---|---|
| `noisy` | 98 | 138 |
| `borderline` | 307 | 138 |
| `quiet` | 182 | 84 |
| `unjudgeable` | 0 | 7 |
| total | 587 | 367 |

The primary metric scores **236 `noisy` against 266 `quiet`** — 502 rows — and reports the 445
`borderline` separately; the 7 `unjudgeable` belong to neither class and their count is the
ground truth's own coverage, 99.3%.

**Utrecht is 37.6% `noisy`, Amsterdam 16.7%, and the cities are not the difference — the
templates are.** The Amsterdam disc is dominated by APV objectvergunning (125 rows, all
`borderline` because the subtype never names the works) and by hospitality permits (`quiet`),
while Utrecht's omgevingsvergunning carries a `Toelichting` that describes the building work
plainly. A benchmark reported as one pooled number would read this template difference as a
property of the method. Per-block reporting is not optional here.

## Two checks that the labels are internally consistent

**The same activity, published twice, got the same label.** Dutch municipalities publish an
`Aanvraag` and later a `Besluit` for one activity, with the activity text otherwise identical.
Stripping the procedural prefix leaves 142 groups holding two or more rows, and **0 of them
disagree** — so the judgement did not drift with the wording around it.

**The operational rule was applied uniformly.** Eight probes (structural terms, demolition,
trees, groundwork, skin-only terms, legalisation, paper acts, traffic orders) were run against
`activity_text` and their labels compared with what the rule predicts. 15 rows deviated, and on
inspection **all 15 are artefacts of the coarse probe, not label errors** — which is itself
worth recording, because two of them reproduce a known bug class independently:

- `Gravenstraat` matches the groundwork probe's `graven`. Five hospitality permits were flagged
  as excavation **by a street name** — the open ledger item about addresses in indexed text,
  caught here by accident.
- `blindkappen` (the blanking caps on a traffic light) matches the tree probe's `kappen`. Same
  shape as the earlier `Looptijd` matching a test for `Tijd`: **a substring test on a word is
  not a test for that word.**
- Four rows matched the skin-only probe on `kozijn` while their `noisy` basis sat elsewhere in
  the same sentence (`funderingsherstel`, `toevoegen van dakramen`, `vernieuwen van het dak`).

What this audit establishes is that the rule was applied **consistently**, not that the rule is
**correct**. Those are different claims and only the first was measured. A random re-read of 18
rows found nothing to change, which is a sanity check and not a precision estimate.

## The headline measurement: keyword search cannot reach these positives

Searching the obvious noise vocabulary — `geluid|lawaai|herrie|muziek|nachtwerk|decibel|db` —
against the labelled set:

| | rows | hits in `title`+`abstract` | hits in `activity_text` | hits in `body_text` |
|---|---|---|---|---|
| `noisy` | 236 | **15** | 26 | 36 |
| `borderline` | 445 | 1 | 1 | 222 |
| `quiet` | 266 | 1 | 1 | 33 |
| `unjudgeable` | 7 | 0 | 0 | 0 |

**89–94% of genuinely noisy publications contain no noise word anywhere in the text an index
would hold.** A keyword search of the indexed text reaches 15 of 236 — recall 6.4%. The activity
field raises that to 26 of 236, 11.0%.

Going to the full body makes it worse, not better: 291 documents match, of which 36 are `noisy`
— **precision 12.4%**. That is the APV glossary doing the damage, now quantified on labels
rather than on a 9-document sample: every APV publication prints the definition of
`Nachtwerkontheffing`, complete with `lawaai` and `70Db`, so the term carries no information
about the document that contains it.

This is what makes the comparison worth running. The positives are things like
`het inzetten van een mobiele hijskraan voor hijswerkzaamheden ten behoeve van renovatie van het
Paleis op de Dam`, `boomstobbe verwijderen met een knijperauto`, `het vervangen van Tramspoor`,
`Kungfu demonstratie & diverse vuurwerk show` and `funderingsherstel doormiddel van
schroefinjectie` — unmistakably loud, and not one of them contains a word a noise query would
think to ask for. Whether embeddings can close that gap is now a measurable question with a
fixed answer key. **No retrieval method has been run against it yet; the numbers above are
properties of the corpus, not a result.**

## Where it lives, and the limits to state with it

`migrations/0014_noise_label.sql` creates `noise_frame`, `noise_frame_member`, `noise_label` and
the `noise_frame_unlabelled` check. The data is **not** a seed migration, unlike
`0004_poi_category_seed.sql` and `0012_noise_impact_rule_seed.sql`: both label tables carry a
foreign key to `bekendmaking`, migrations run on every api boot, and a seed holding 954
identifiers would fail the boot on any database where the bekendmaking import has not run. It
lives in `docs/pg-notes/noise-frame.csv`, `noise-frame-member.csv` and `noise-labels.csv`
(diffable, 144 KB) and is loaded by `scripts/load_noise_groundtruth.py`, which refuses a partial
load rather than coming up silently short. Re-running it is idempotent — verified, same
distribution and zero unlabelled. The refusal path itself has not been exercised.

Limits, so no later claim overreaches them:

- **It measures whether the activity is loud, not whether it will disturb anyone.** Time is
  outside the concept by design, so a three-day daytime demolition and a sixty-day night-work
  permit score alike. The period columns from the section above are what a later version uses.
  The clearest cost: a horeca `exploitatievergunning` reading `Openingstijden Vrij-Za: 7.00 tot
  4.00` is `quiet` here, and a bar open until four is exactly what a resident would complain
  about.
- **`borderline` is 46.6% of the frame.** The two largest blocks of it are structural, not
  hedging: 125 Amsterdam objectvergunning rows whose subtype asserts works without naming them,
  and the facade/frames/signage family. The primary metric excludes them and says so; a reader
  who wants a binary can collapse them either way and see how much moves.
- **A withdrawn or refused application is labelled on the activity it describes, not on the
  outcome.** `Afgebroken (Ingetrokken): het houden van event Koningsdag ... | Buurtfeest` is
  `noisy`. Labelling it `quiet` would punish a method for correctly retrieving a document about
  a loud event, and "will it actually happen" is a second concept needing its own parse of the
  `Besluit:` states. `basis` records the withdrawal on each such row.
- **Two 300 m city-centre discs are not the Netherlands.** Both are dense pre-war centres heavy
  on renovation. Nothing here speaks to suburbs, industry or new-build areas.
- **The source truncates.** Amsterdam's `Activiteiten` field ends mid-sentence in the published
  XML itself (`Er wordt gedanst op kleine schaal binnen de beschikbare`) — verified against the
  raw `<al>` element, so it is an upstream limit on what any method can know, not our loss.

## The extractor this needed, and three defects in it

`migrations/0015_bekendmaking_activity.sql` is the view that answers "what is being done", and
it was the real prerequisite: `title` + `abstract` average 174 characters and for 43.1% of
Amsterdam's 12,882 omgevingsvergunning rows name no activity at all.

Getting it to that point needed the body of every publication, so the body importer's scope was
widened. The scope named `all` was a lie — it meant `apv OR evenement` and skipped 37,480 rows,
61% of the corpus, including the whole omgevingsvergunning family. It is now `period` (the
honest name for what it selected) plus a real `corpus`, which is the default. The widened run
fetched **37,480 bodies, 36,560 `no_period`, 919 `ok`, 1 `empty_body`** — the first non-ok status
in the corpus, 0.0016% — so all 61,853 publications now have a body.

The view went through three revisions, and the first two were avoidable:

1. **`(.+)` instead of `([^\n]+)`.** In Postgres regex `.` matches a newline unless the `n` flag
   is set, so each field captured itself *and the entire rest of the document*: a mean
   `activity_text` of 1208 characters where the field is 82. Boilerplate swamping the signal
   again, this time self-inflicted.
2. **One field, discovered one at a time.** The first version read only `Omschrijving`,
   `Toelichting` and `Naam evenement`, and then `Activiteiten` turned up — the field that says
   `live DJ-sets`, `Buurtfeest`, `Buiten muziek en een tap`, `Kungfu demonstratie & diverse
   vuurwerk show`, i.e. the entire basis for judging events. Finding fields by sampling was the
   error. A census fixed it: every `^<Capitalised label>:` line across all 61,853 bodies,
   counted. Six are descriptive (`Omschrijving` 15050, `Toelichting` 4628, `Onderwerp` 1742,
   `Activiteiten` 1590, `Betreft` 915, `Naam evenement` 479); the high-frequency ones —
   `Object`, `TVM`, `Nachtwerkontheffing`, `Winkeltijdenontheffing`, each in 22,664 bodies — are
   the APV glossary, printed in every APV publication. **Enumerate the closed set once instead
   of discovering it a field at a time.**
3. **The fallback threw away the only distinguishing text.** With no labelled field the view
   took the body's first line, which is sometimes pure boilerplate: 8 frame rows all opened
   `Het college van burgemeester en wethouders van Amsterdam,` and were indistinguishable,
   while their titles separated a disabled-parking-bay order from `Mededeling opruimen gezonken
   vaartuig` — salvaging a sunk vessel from a canal, which is crane work. The title now goes in
   front of the body line, and those 8 rows split into 8 distinct texts.

Coverage after all three: **91.1% of the corpus gets a labelled descriptive field**, 8.9% falls
back to title plus first body line.

## The one quantitative field does not order the corpus by noise

`bekendmaking_body.visitors_peak` is the only number in this corpus that looks like a noise
proxy, and every Amsterdam event publication carries it. The temptation is to set a threshold
and be done. The data refuses:

| event | visitors at peak | what the `Activiteiten` field says | label |
|---|---|---|---|
| Food For Thought | 1000 | barbecue, live muziek optredens, dansen | `noisy` |
| The Sweet Spot – ADE | **75** | Bezoekers kunnen luisteren naar **live DJ-sets** | `noisy` |
| Lichtjes aan Kerstboom | 250 | speech, **Brassband Ritmo en de Gospel Queens** | `noisy` |
| Koningsdag – Weinlokal Stern | 50 | **barbecue en tappunt** | `borderline` |
| Capture360 De Dam | 5 | fotostage met robotarm, max 4 personen | `quiet` |

A 75-person Amsterdam Dance Event party is louder than a 1000-person food market, and the count
cannot say so. Crowd size and sound are different axes, so no threshold on `visitors_peak`
reproduces these labels — the free-text `Activiteiten` field does. Recorded because it closes
off the cheapest-looking shortcut: **the structured number cannot substitute for reading the
text**, which is another reason the retrieval question here is a real one.
