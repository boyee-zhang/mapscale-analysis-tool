# FTS baseline against the noise ground truth

Run 1, 2026-11-03. Harness `scripts/experiments/noise_retrieval_bench.py`, raw output in
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
on strict, against a floor of 0.640 — and it transfers *upward* to the held-out block, 0.786
Amsterdam -> **0.869 Utrecht**.

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
- `bekendmaking_fts_tmp` is still in no migration. Every FTS arm here used it, so the
  benchmark is not reproducible on a fresh database until it is promoted.
