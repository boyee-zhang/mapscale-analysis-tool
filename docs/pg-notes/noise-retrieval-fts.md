# FTS baseline against the noise ground truth

Run 1, 2026-11-03. Harness `etl/experiments/noise_retrieval_bench.py`, raw output in
`raw/noise-retrieval-fts-run1.txt`. The answer key is the 954-row hand-labelled frame built in
`noise-groundtruth.md`; nothing here was measured against rules or against pooled judgements.

The task is one query, one concept: *this publication announces a loud activity*. The frame is
frozen and exhaustively labelled, so **recall is a real number**, not the lower bound a
benchmark normally has to live with.

---

## What the numbers are allowed to mean

**Three universes, all three printed.** `borderline` is 445 of 954 rows and is the one
subjective cut in the label set, so no single grading is trustworthy on its own:

| universe | corpus | relevant | n | base rate |
|---|---|---|---|---|
| `strict` | noisy + quiet | noisy | 502 | 0.470 |
| `wide` | all judgeable | noisy | 947 | 0.249 |
| `lenient` | all judgeable | noisy + borderline | 947 | 0.719 |

`unjudgeable` (7 rows) is never relevant and never retrieved. Collapsing it into `quiet` would
pay a method for failing to find a document whose answer nobody knows.

**`all` is the floor, not a formality.** A method that retrieves the entire frame scores
precision = base rate and recall 1.0. On `strict` that is **F1 0.640**. Any arm below it is
worse than not filtering at all, and most arms here are.

**Contamination is declared per arm.** Every label was read by the author who would then write
the queries, so `tuned` says what a query is worth:
- `no` — the vocabulary predates the labels (`migrations/0012`) or is what a resident plainly
  types. Honest everywhere.
- `dev` — a decision made after reading the labels. Its only honest number is the held-out
  block, `utr-binnenstad-300`, whose publication templates differ from Amsterdam's.

---

## The table (universe `strict`, both blocks, n=502, 236 relevant)

| arm | tuned | ret | tp | fp | prec | recall | F1 |
|---|---|---|---|---|---|---|---|
| `all` — the floor | no | 502 | 236 | 266 | 0.470 | **1.000** | **0.640** |
| `naive_fts:ta` | no | 6 | 6 | 0 | 1.000 | 0.025 | 0.050 |
| `naive_re:ta` | no | 16 | 15 | 1 | 0.938 | 0.064 | 0.119 |
| `naive_re:body` | no | 69 | 36 | 33 | 0.522 | 0.153 | 0.236 |
| `rules_fts:ta` | no | 14 | 14 | 0 | 1.000 | 0.059 | 0.112 |
| `rules_re:ta` | no | 76 | 73 | 3 | 0.961 | 0.309 | 0.468 |
| `rules_fts_pre:ta` | no | 75 | 73 | 2 | 0.973 | 0.309 | 0.469 |
| `cls_literal` | no | 103 | 83 | 20 | 0.806 | 0.352 | 0.490 |
| `cls_works` | **dev** | 220 | 190 | 30 | 0.864 | 0.805 | **0.833** |

Per block, and this is where a single number would have lied:

| arm | AMS (n=280, base 0.350) | UTR held out (n=222, base 0.622) |
|---|---|---|
| `all` floor | F1 0.519 | F1 0.767 |
| `rules_re:ta` | F1 **0.697** | F1 **0.250** |
| `cls_works` | F1 0.786 | F1 **0.869** |

---

## Four findings

### 1. Every hand-written noise query is worse than retrieving everything

Best keyword-vocabulary arm anywhere: `cls_literal`, **F1 0.490**, against a floor of 0.640.
The shape is always the same — **precision 0.94–1.00, recall 0.03–0.35**. Keyword search on
this concept is a high-precision, near-zero-recall instrument: what it returns is right, and it
returns almost nothing.

The naive query is not a strawman. `geluid|lawaai|herrie|muziek|nachtwerk|decibel|db` is the
only vocabulary the concept *has* a word for, and over what production indexes today it finds
**15 of 236** loud publications. The positives it misses read like this, and none of them
contains a word for noise:

> `mobiele hijskraan … hijswerkzaamheden`, `boomstobbe verwijderen met een knijperauto`,
> `het vervangen van Tramspoor`, `funderingsherstel doormiddel van schroefinjectie`,
> `vervangen van de draagconstructie`, `Kungfu demonstratie & diverse vuurwerk show`

### 2. FTS was a strict subset of regex, and prefix matching closed the gap exactly

Over `title || abstract` on the strict universe, the same vocabulary as `~*` and as
`@@ to_tsquery('dutch', …)`:

```
both = 14      regex-only = 62 (59 noisy)      fts-only = 0
```

Not a single row was reached by FTS that regex missed. The cause is Dutch compounding: the
permit type is a compound whose head is `-vergunning` / `-ontheffing` / `-voorschriften`, and
Snowball does **not** decompound, so one lexeme covers the whole word.

```
evenementenvergunning -> 'evenementenvergunn'    does NOT match the lexeme 'evenement'
geluidvoorschriften   -> 'geluidvoorschrift'     does NOT match 'geluid'
hijskraan             -> 'hijskran'              does NOT match 'kraan'
```

Those 62 rows are overwhelmingly `evenementenvergunning`. A regex substring reaches inside a
compound; a tsquery lexeme cannot.

Stemming pushes the other way at the same time, which is why "FTS is worse" would be the wrong
lesson. Per-morpheme over the frame:

| morpheme | regex | tsquery | why |
|---|---|---|---|
| `kraan` | 5 | 1 | `autolaadkraan`, `bovenlaadkraan`, `hijskraan` — head-final compounds |
| `graafwerk` | 2 | 0 | `graafwerkzaamheden` |
| `sloop` | **0** | **3** | stemming folds `slopen`/`sloop` -> `slop`; the substring was absent |

**Writing every term as a prefix recovers the whole deficit**: `rules_fts_pre:ta` reaches the
same 73 true positives as the regex at slightly better precision (0.973 vs 0.961). Dutch puts
the modifier first in `evenement|envergunning`, so `evenement:*` reaches it. Nothing reaches
`hijskraan` from `kraan` — there the loud morpheme is the head — but that is 1 row here.

So the honest statement is **not** "FTS loses to regex". It is: *a tsquery written without `:*`
loses 5x the recall in a compounding language, and the fix is free.*

And FTS is what you should run, because it is the same answer 76x faster. Whole corpus,
61,853 rows, third run of three:

| | rows | time |
|---|---|---|
| `@@ to_tsquery('dutch','evenement:* \| geluid:* \| nachtwerk:*')` | 2667 | **1.8 ms** (GIN) |
| `~* 'evenement\|geluid\|nachtwerk'` | 2671 | **136.6 ms** (seq scan) |

The 4-row difference is the stemming boundary, not an error. This is the one unambiguous thing
FTS buys on this corpus: not quality, latency.

### 2a. What each arm actually searches — and `cls_works` barely searches text

The arm suffix is the field: `:ta` is `title || abstract` (what `bekendmaking_fts_tmp` indexes
in production), `:act` is `activity_text` (the descriptive fields migration 0015 extracts from
the body), `:body` is the whole document. `cls_works` and `cls_literal` have no suffix, and
that is not a formatting detail — they go through `noise_impact_rule`, which matches on three
different things. Splitting their 256 true positives by which one fired:

| matched_on | kind | tp | fp | share of tp |
|---|---|---|---|---|
| `activiteit` | structured array | **157** | 12 | **61%** |
| `doc_type` | structured field | 66 | 24 | 26% |
| `text` (title‖abstract regex) | text | 33 | 1 | **13%** |

The single largest contributor is one row of the rule table: `activiteit = 'bouwen'`, **125 tp /
12 fp**. That is a word lookup in an enum array, not a search.

**This corrects the attribution in section 3 below, which was written before this breakdown
existed.** Run 1 credited the `undeterminable` bucket's strength to its APV text patterns —
`objectvergunning` / `\yObject\y` / `TVM`. Those rows are almost all `borderline`, so the
strict universe *removes* them, and in the strict universe `TVM` contributes **1** true
positive. What carries that bucket is `activiteit = 'bouwen'` at priority 95. So the honest
reading of 0.845 is: *this publication is tagged as a building activity, or it is an event
permit, or (13% of the time) its title mentions something loud.* A metadata classifier with a
small text assist — and listing it in one table beside the FTS arms, as run 1 did, invited
exactly the wrong comparison.

**The text-only ceiling, three blocks, strict (n=695, base rate 0.450, floor F1 0.621):**

| what is searched | best arm | prec | recall | F1 |
|---|---|---|---|---|
| `title + abstract` | `rules_re:ta` | 0.926 | 0.319 | **0.475** |
| `activity_text` (from the body) | `rules_re:act` | 0.920 | 0.220 | 0.356 |
| whole `body_text` | `rules_re:body` | 0.636 | 0.262 | 0.371 |
| structured metadata | `cls_works` | 0.874 | 0.818 | 0.845 |

So **no text method ever cleared the floor.** The best is 0.475 against 0.621. The one arm that
cleared it was not reading text. Finding 1 above said "every hand-written noise query is worse
than retrieving everything"; this is the sharper version — *every* text method is, whatever
field and whatever matcher.

And the smallest field beats the largest two: `title+abstract` (169 chars average) scores 0.475
where the full body (2453 chars) scores 0.371. The title carries the **permit type name** —
`evenementenvergunning`, `nachtwerkontheffing` — while the body carries the APV glossary that
every APV publication prints. More text, worse signal-to-noise. That is also why the embedding
round must fix its input field by design rather than defaulting to "the longest text available".

### 3. The best keyword method does not describe the activity at all

`migrations/0012` is a classifier with priorities, and reading it as retrieval means choosing
which classes count as a hit. Scored against the labels on the strict universe:

| 0012's class | noisy | quiet |
|---|---|---|
| `explicit` | 15 | 1 |
| `loud_activity` | 19 | 0 |
| `event` | 49 | 19 |
| **`undeterminable`** | **107** | **10** |
| `none` | 18 | 167 |
| `unlabelled` (matched nothing) | 28 | 69 |

`undeterminable` was created as the honest *"the index record cannot answer this"* default for
`objectvergunning` / `TVM` / `activiteit = bouwen`. It is **91% noisy**, and it is by a wide
margin the strongest signal in the table. Counting it as a hit (`cls_works`) gives **F1 0.833**
on strict, against a floor of 0.640.

One correction to how that was first written up here. The cross-tab above was computed over
*both* blocks, so Utrecht was **not** held out for this arm — the decision to count the bucket
as a positive saw its labels too. The decision is recoverable from the development block alone
(Amsterdam's `undeterminable` is 22:3, 88.0% noisy, which drives the same choice), but that was
checked afterwards, not done in that order, and a figure called held-out has to be earned by
the process rather than by a later audit. The generalisation section below supplies a block
that genuinely was not seen when this decision was made.

Read that carefully, because the flattering reading is wrong. `cls_works` does not identify
loud activity. It identifies **that works are happening at all** — a scaffolding permit, a
parking-bay closure, a building activity code — and in a 300 m circle of a Dutch city centre,
works are usually loud. The predictive content is the *permit category*, which is a tiny closed
vocabulary that both regex and FTS match perfectly, and which contains **no description of the
activity whatsoever**.

Its apparent strength is also partly the grading. Under `wide` it collapses to **precision
0.321** (402 of its 592 hits are `borderline`) for F1 0.459 against a floor of 0.399 — because
`borderline` *is* "works asserted but never named", exactly the population `cls_works` cannot
separate. It clears the floor in all three universes (0.833 / 0.459 / 0.883) but only `strict`
and `lenient` by a margin worth anything.

**So the bar embeddings have to clear is 0.833, not 0.490** — and the interesting question is
no longer "can a method beat keywords", it is "can a method separate the 445 borderline rows
that the permit category lumps together".

### 4. A precedence rule written for precision destroys recall

0012 orders its rules so an explicit signal wins. Two `none` rules at priority 40 sit *above*
the works defaults and suppress true positives outright:

```
'horeca|drank|terras|exploitatie'   suppressed 12 noisy rows
'splits'                             suppressed  6 noisy rows
```

The mechanism is multi-activity documents. `splitsen en omzetten van een deel van het gebouw
naar drie appartementen, het maken van een dakterras en twee balkons en het plaatsen van
dakramen` is a split **and** a roof terrace **and** two balconies — the rule fires on the one
word that means "paper only" and stops. Any priority-ordered classifier over a document that
describes several activities will do this; it is not a bad rule, it is a single-label answer to
a multi-label question.

---

## The generalisation test: a third block, and a prediction that failed

Run 1 closed with a claim of mine: that `cls_works` is strong only because of the frame — *"in
a 300 m circle of a Dutch city centre, works are usually loud"* — and that its predictive power
would fall away in a suburb. That is a falsifiable statement, so it was tested rather than left
standing.

**The block.** `ams-osdorp-600`: Osdorp, Nieuw-West — post-war garden-city Amsterdam, 600 m
rather than 300 m because the publication density is about a fifth of the centre's. 362
publications, 200 distinct activity texts, labelled one at a time with the **identical** rule
and with precedent taken from the existing labels wherever a phrase already had one. Result:
**77 noisy / 169 borderline / 116 quiet / 0 unjudgeable**, base rate 0.399 on the strict
universe. `noise_frame_unlabelled` returns 0.

It is structurally a different corpus, not just a different place:

| | canal belt | Osdorp |
|---|---|---|
| `verkeersbesluit` | 5 of 587 (0.9%) | **54 of 362 (15%)** |
| tree felling | none | 20 rows, 12 distinct texts |
| `exploitatievergunning` | 96 | 36 |
| dominant works family | funderingsherstel, werfkelders, dakkapellen | dakopbouwen, woningvorming, maaiveld |

**The result, and it is not what was predicted.** `cls_works` F1 by block:

| universe | canal belt (dev) | Utrecht | **Osdorp (new)** | all three |
|---|---|---|---|---|
| strict | 0.786 | 0.869 | **0.880** | 0.845 |
| wide | 0.336 | 0.611 | **0.441** | 0.454 |
| lenient | 0.886 | 0.878 | **0.919** | 0.893 |

Osdorp is the **best** of the three under strict and lenient, and `cls_works` clears the `all`
floor in **9 of 9** block × universe cells. The prediction was wrong: this method does not
depend on city-centre density. Precision is in fact *higher* in the suburb (0.904 vs 0.786) —
the canal belt's false positives are event permits for things that are quiet, such as a
`Tentoonstelling`, and Osdorp's events are kermis, Pride and a festival, which are not.

**The part that is genuinely interesting is the stability.** Strict F1 across the three blocks:

| arm | range | spread |
|---|---|---|
| `naive_re:ta` | 0.000 – 0.265 | unbounded |
| `rules_re:ta` | 0.250 – 0.697 | 2.8x |
| `cls_literal` | 0.333 – 0.729 | 2.2x |
| `cls_works` | **0.786 – 0.880** | **1.12x** |

And the components of `cls_works` are *not* stable — `cls_undet_only` recall is 0.247 in Osdorp
and 0.616 in Utrecht, a 2.5x swing. Each block's publication template files the same physical
work under a different bucket, and the buckets compensate for each other. **The union is
robust; no part of it is.** That is an argument for the union and against ever reporting one of
its components as the finding.

So the bar for any later method is **0.845 over 695 rows and three blocks**, not 0.833 over
two, and it is a bar that has now survived a deliberate attempt to break it.

### One thing this block caught that the method had been getting away with

Two Osdorp publications share the activity text `het afwijken van het omgevingsplan ten behoeve
van het realiseren van 4 woongebouwen met 277 appartementen` and differ only in `activiteit`:
one is `{ruimtelijke ordening}` (the planning deviation, a paper act) and the other is
`{bouwen}` (the building permit). Labelling keyed on the text alone would have given both the
same answer, and one of them would have been wrong.

The committed 954 were audited for the same failure: **15 activity texts spanning 70 rows** have
more than one distinct `activiteit`. In every one of the 15 the label is unchanged across the
group — the variation is `{bouwen}` vs `{bouwen,bouwen}`, or an added `reclame` /
`ruimtelijke ordening` on signage, none of which moves the answer. So the existing ground truth
is unaffected, and the Osdorp pair is labelled per identifier rather than per text.

## What this does not measure

- **Loud, not disturbing.** The ground truth dropped time on purpose (`noise-groundtruth.md`).
  A 3-day daytime demolition and a 60-day night-work permit score alike.
- **One concept, one query.** No claim is made about multi-concept or free-text search.
- **Two blocks of one country, 2026 only.** Amsterdam is 16.7% noisy and Utrecht 37.6% because
  the templates differ, not the cities — which is why every figure above is also given per
  block.
- **`ts_rank_cd` does no document-frequency weighting**, so AP for the FTS arms (printed in the
  raw output) describes the matched set under a near-arbitrary order. For a boolean OR query
  P/R/F1 do not depend on the ranking, so this does not affect anything above. It must be fixed
  before any ranked comparison, so that a later method is measured against a *fixed* ranker
  rather than this one.

## Open, surfaced

- **Unexplained:** a Bitmap Heap Scan reporting `Heap Blocks: exact=1856` accounts for only
  `shared hit=182` buffers, reproducibly, PG 16.4. The counter is not at fault — a forced seq
  scan of the same relation reports `hit=3281 read=5778` = 9059 = exactly
  `pg_relation_size/8192`. The first calibration attempt was invalid and nearly went in as a
  finding: it filtered the plan text and never noticed the planner had answered `count(*)` from
  an **Index Only Scan**, so the "3.2x under-report" it appeared to show was a different node.
  Cause still unknown. No claim above depends on it; the latency figures are wall clock.
- **Fixed while building the third block:** `etl/ingest/noise_groundtruth.py` read
  `os.getenv("DATABASE_URL")` directly and never loaded `.env`, so it ran only in a shell that
  happened to export it — unlike every other script here, which reaches the DSN through
  `api.main.db`. It now does the same. The CSVs round-trip: export, load, 0 unlabelled.
- `bekendmaking_fts_tmp` is still in no migration. Every FTS arm here used it, so the
  benchmark is not reproducible on a fresh database until it is promoted.
