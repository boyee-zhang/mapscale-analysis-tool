# `tussenkop`: what the body parser was dropping, and what recovering it is worth

Measured 2026-11-12 against `bekendmaking` / `bekendmaking_body` at N = 94,183 rows.
Branch `fix/bekendmaking-tussenkop`.

This note records a conclusion that was **wrong twice before it was right**, so the shape of
the error is kept rather than cleaned up.

## The entry this started from

> **open, deferred 2026-11-10** `etl/ingest/bekendmaking_body.py` is still regex (`_AL`, `_TAG`)
> and drops `tussenkop` — 6.3% of body text. Switching costs 94,183 single-document fetches;
> the 24-doc sample says those headings are appeal boilerplate. Widen the sample before paying.

All three numbers in it turned out to be wrong, and the disposition rested on them.

## First answer (wrong): "0.41%, all boilerplate, do not pay"

A stratified sample — 50 documents from each of the six `doc_type` strata covering 92,385 of
94,183 rows, picked by `md5(identifier || seed)` — was fetched, and every `tussenkop` in it was
labelled by five independent workers against one shared spec (no worker saw another's shard or
computed any rate; the stratum weights live only in the aggregator).

```
doc_type                        N_corpus  incidence  headings  pooled_chars_lost
evenementenvergunning             34,481       4.0%         3             0.09%
andere vergunning                 32,587      92.0%        92             1.097%
omgevingsvergunning               17,813       2.0%         3             0.065%
verkeersbesluit of -mededeling     3,425      78.0%        41             0.123%
exploitatievergunning              2,748      52.0%        52             0.62%
omgevingsmelding                   1,331       0.0%         0             0%
```

191 headings: 149 `boilerplate_procedural`, 42 `navigational_duplicate`, **0
`substantive_unique`**, 0 `unclear`. Weighted character loss 0.456%.

Three things were wrong with this, and the first two were found by inspecting it rather than by
the fan-out:

- **The 191 headings are only 8 distinct texts** (73x `Niet mee eens?`, 73x
  `Voorlopige voorziening`, 39x `Besluit:`, 2x `Met inachtneming van:`, four singletons). The
  effective sample size for the judgement was 8. Fanning 191 units across five agents cost
  ~358,000 tokens to decide 8 strings; de-duplicating first would have cost one agent and
  seconds. **Before fanning out, ask how many units survive de-duplication.**
- **gemeente was the axis not varied.** Of the 114 heading-bearing documents, 110 were
  Amsterdam.
- **`doc_type` was never the real axis.** Holding `doc_type = 'andere vergunning'` fixed and
  varying gemeente: Amsterdam 45/50 documents carry a heading, Utrecht+Diemen **0/50**.
  `andere vergunning` is 31,852/32,587 Amsterdam (97.7%), so the "92% incidence of this
  doc_type" was a publisher property wearing a doc_type's clothes. Corpus-wide incidence is
  Amsterdam 63.0% against 5.8% elsewhere (n = 400 each).

## The counterexample that destroyed it

A 2-document sweep of all 294 gemeenten (580 fetched) found 37 distinct heading texts in 18
gemeenten, including `Datum evenement`, `Datum ontvangst`, `Datum besluit`, `Soort procedure`,
`Dossiernummer`. An exhaustive fetch of Nissewaard (24 rows) and Rhenen (65) found 44 documents
whose heading text appears nowhere in the stored body.

The decisive row, verified by hand:

```
gmb-2026-209074  (Nissewaard, evenementenvergunning)
  tussenkop: Waar | Datum en tijd ontheffing
           | 18 mei 2026 t/m 20 mei 2026 mini 15.30 – 19:00 uur en 21 mei 2026 17:00 – 22:00 uur
           | 15 juni 2026 t/m 18 juni 2026 avondvierdaagse 17:00 – 22:00 uur
           | Voorschriften | Dossiernummer | Datum ontvangst | Datum besluit | Bezwaar
  stored row: status='no_period', start_date=NULL, end_date=NULL, period_raw=NULL
  body_text contains 'mei 2026' / 'avondvierdaagse' / '17:00' : false / false / false
```

The event period was **only** in the headings. The row reported the document as not stating a
period while the document stated it plainly. The 191-heading sample could not see this because
96.5% of its heading-bearing documents were Amsterdam, where 491 headings are 3 distinct texts
and all of them really are appeal boilerplate.

## Second answer (also wrong): "10,591 rows lose their core field"

`evenementenvergunning` is 3/1,607 `no_period` in Amsterdam (0.2%) and 10,591/32,874 elsewhere
(32.2%), a 160x gap. That gap is real. **Attributing it to the dropped headings was not
measured — it was extrapolated from 44 rows, and it is wrong.**

Running the fixed patterns against the stored `body_text` of 3,000 random `no_period` rows:
`_LOOPTIJD` 0 hits, `_DATUM_VAN` 0, `_DATUM_TM` 0, `_DATUM_EN_TIJD` 0. The context around a
Dutch-month date in those bodies says why:

```
  47x  '... Het bezwaarschrift moet uiterlijk'      24x  '... Op'
  15x  '... Besluiten: met ingang van'              15x  '... Gepubliceerd op'
  11x  '... De gemeente heeft op'                    6x  '... Datum ontvangst aanvraag:'
```

Those are publication, decision, receipt and objection-deadline dates. **Those documents do not
state an event period at all** — which is exactly why `etl/derive/event_dates.py` exists. An earlier
diagnostic put the fixable set at 5,318 rows by testing "the text contains a period keyword";
the real patterns require the keyword to be *adjacent to* a date, and that predicate was looser
than the code. A diagnostic must use the production pattern or it will invent a payoff.

## What the fix is, and what it is actually worth

`etl/ingest/bekendmaking_body.py`:

- **Body text is read with ElementTree, and includes `<tussenkop>` interleaved with `<al>` in
  document order.** Same reason 156e8fe switched the SRU record parser. While measuring this
  bug, a single-pass regex `<(tussenkop|al)\b[^>]*>(.*?)</\1>` reported 9 headings where two
  independent per-tag scans found 41 — it swallows a heading nested inside an `<al>`. A parser's
  failure mode is an exception; a regex's is a plausible wrong answer. Ordering is load-bearing:
  several publishers put a field label in one heading and its value in the next.
- **Dates accept both published formats.** Amsterdam writes `22-05-2026`; most other
  municipalities write `18 mei 2026`, which the numeric-only pattern could never match.
- **`_DATUM_EN_TIJD`**, a label whose value sits on the following line — only matchable at all
  now that headings are in the body text.
- `_unesc` is gone (ElementTree handles XML entities; `_pre` handles the HTML ones the source
  ships, which otherwise make ElementTree raise).
- Malformed XML now yields `parse_error` instead of whatever the regex could salvage.

Measured, re-fetching real documents and running the new parser against the stored rows:

| | result |
|---|---|
| `ok` rows that already parsed (n=122, Amsterdam 100 + elders 22) | **122 still `ok`, all dates identical, 0 changed** |
| random `no_period` rows (n=200, half Amsterdam) | 0 gained a period |
| exhaustive Nissewaard + Rhenen (n=89) | **1 gained a period** (`gmb-2026-209074`) |
| `tussenkop` share of stored body characters | 0.60–0.69% (plain random n=400 twice: 0.602%, 0.654%; bootstrap 95% CI 0.579–0.727%) |

So: **the text loss was real and is fixed; the field loss it causes is about 1 row in 89 of the
worst-affected municipality, not thousands.** The 0.41%/0.456% figures from the first answer were
equal-allocation and ratio-of-percentages artifacts — `omgevingsmelding` is 1.4% of the corpus
at 0% loss but was 16.7% of the sample, and per-stratum *percentages* were weighted by document
count while the heading-rich strata have ~3,000-character bodies against 670.

The value of the fix is mostly **semantic, not numeric**: `body_text` feeds the FTS index and the
embeddings, and the recovered labels are what distinguish an event date from a receipt date in
bodies that otherwise read `DIG0055475. / 21 november 2026. / 11:00 uur tot 12:00 uur.`

## Still open

- **A full re-fetch is not justified by the period payoff** (~1%), but is justified by the 0.6%
  of text and the labels, because both reach FTS and the embeddings. `--refetch` already exists.
  Not urgent, and it rewrites all 94,183 rows, which makes that run's `last_changed_run` useless
  as an upstream-change signal.
- **The label vocabulary is Amsterdam's.** `Looptijd` / `Datum van` / `Datum t/m` / `Tijd van` /
  `Datum en tijd` do not cover `Datum evenement`, `Soort procedure`, `Verzenddatum` or the other
  34 heading texts the 294-gemeente sweep found. Widening it needs a per-publisher survey, not a
  guess.
- **Times are still unparsed in the recovered rows.** `gmb-2026-209074` states
  `15.30 – 19:00 uur` with no `Tijd van` keyword, so `start_time`/`end_time` stay NULL while
  `period_raw` keeps the text. Correct under "nothing is inferred", but recoverable.
- **44 is a floor, not an estimate.** The gemeente sweep probed 2 documents per municipality, so
  a publisher using this template in under half its rows was missed. A SQL proxy
  (`body_text ~ '^\s*\d{1,2} <dutch month> \d{4}\.?\s*$'` → 330 rows) over-counts: 4 of them
  (Gulpen-Wittem, Noord-Beveland) put the labels in `<al>` table cells, so their labels survive.
  **Do not use 330.**

## Method notes worth keeping

- **A diagnostic looser than the production pattern invents a payoff.** 5,318 became 0.
- **De-duplicate before fanning out.** 191 units, 8 distinct values, ~358,000 tokens.
- **`doc_type` can be a proxy for publisher.** Stratifying on it looked like varying the axis
  that mattered and did the opposite.
- **A status written by another process is not evidence about this one.** The first before/after
  run reported 100 regressions; all 100 were `ok_derived` rows whose dates come from
  `etl/derive/event_dates.py` and never passed through `parse_body`.
- An empty stratum reads exactly like a finding: `$$ x $$` with padding selected 0 rows and
  printed a census of zeros. The sampler now raises instead.
