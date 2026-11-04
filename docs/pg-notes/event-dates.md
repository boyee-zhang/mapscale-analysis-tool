# Extracting the event date from 294 municipal templates — measured 2026-11-04

The national import gave 34,481 event publications but no event dates: the period extractor in
`import_bekendmaking_body.py` reads Amsterdam's labelled APV template (`Datum van:`,
`Bezoekers drukste moment:`) and 50 of 50 national bodies returned `no_period`
(`event-sources.md` §7). This is the attempt to recover the date everywhere else.

**Result, on a fold never used for design: precision 89.5%, recall 58.6%, zero fabrications.**
Not good enough to print a date unconditionally. Good enough to print one beside the evidence
that produced it. The decision is in §6.

## 1. Why this is not "find a date in the text"

A body carries **2.2 dates on average** (measured, 850 bodies, 195 municipalities) and most are
administrative. The leading context separates them cleanly, which is the whole basis of the
method:

| administrative — must not be taken | the event |
|---|---|
| `de gemeente heeft op` 33 | `datum evenement:` 11 |
| `besluit is verzonden op` 22 | `van … tot en met` 16 |
| `weken en eindigt op` 21 | `dit evenement vóór` 13 |
| `datum ontvangst aanvraag:` 11 | `en plaats te vinden:` 8 |
| `de bezwaartermijn eindigt op` 8 | `datum: … eind datum:` 7 |

Achtkarspelen opens `Op 02-03-2026 is de volgende aanvraag binnengekomen: Singelland VO
Surhuisterveen schooltriatlon`. A first-date-wins rule returns the **receipt** date and looks
entirely plausible. So the rule is the opposite: a bare date is never taken — something nearby
has to say what it is, and `basis` records which pattern said so.

## 2. How the folds were cut, and why by municipality

One template serves a whole municipality, so splitting by document would put the same template
on both sides and the measurement would be of memorisation. Split on `md5(gemeente)`: dev 414
documents / 100 municipalities, test 436 / 95, **overlap 0**. Test was then split again — fold
A (44 municipalities) for one honest measurement of v1, fold B (30 further municipalities)
kept sealed for the final number.

## 3. v1, and three defects worth keeping

v1 was written from seven dev documents. On fold A: **precision 85.7%, recall 30.0%.**

- **The veto was checked at the wrong offset.** `_vetoed` inspected the run-up to the start of
  the whole match — the event noun — rather than to the date. So
  `evenementenvergunning ontvangen op 7 januari 2026` passed a veto list containing
  `ontvangen`, because nothing administrative precedes `evenementenvergunning`. **A guard that
  inspects the wrong span is worse than no guard: it reports a clean run.**
- **Dutch writes a range with the month once.** `van 5 t/m 7 juni 2026`, `op 26 en 27 april
  2026` — the first day carries no month or year. A pattern demanding two complete dates misses
  every one, and worse, silently returns the *second* day as the start.
- **A weekday sits between the preposition and the date**: `op zaterdag 28 februari 2026`.

v2 fixes all three. On fold A: **96.7% / 69.0%**.

## 4. Two of my own labels were wrong, both the same way

I labelled from a 300-character body excerpt and a 110-character title. Twice that produced a
confident "no event date" that was false:

- **Haarlem** — the title, truncated in my print, carried `het evenement Haarlem Culinair 2026,
  op 30-07-2026`. The extractor was right; my measurement scored it as a fabrication.
- **Dinkelland** — a *rectification*: `per abuis is in de publicatie van 26-02-2026 de datum
  24-05-2026 genoemd maar dit had 14-05-2026 moeten zijn`. The real date is 14-05, and the
  document states the **wrong** date prominently in order to correct it. A naive extractor
  takes 24-05 and is confidently wrong in a way nothing downstream could detect.

The same trap caught two more in fold B (Tubbergen's `Wanneer: van 25 t/m 28-06-2026`,
Rotterdam's `Datum: maandag 27 april 2026`), both beyond the excerpt. **Excerpt-based
labelling is unreliable; every "no date" label must be confirmed by listing every date in the
full text programmatically.** After that change, fold B has exactly one true no-date document.

## 5. The final number, and what the gap between the folds means

Fold B — 30 municipalities, never looked at while any pattern was written:

| fold | role | precision | recall |
|---|---|---|---|
| A (44) | measured v1, then tuned v2 | 96.7% | 69.0% |
| **B (30)** | **sealed until the end** | **89.5%** | **58.6%** |

**The drop is the overfitting, and it is why fold B existed.** Quoting 96.7/69.0 as the
extractor's accuracy would have overstated precision by 7 points and recall by 10. Zero
fabrications on both folds is the one property that held.

The two fold-B date errors are the same defect, not two:

- **Westland** `verleend op 4 maart 2026` → taken as the event date. The veto has
  `verzonden`, `ontvangen`, `bekendgemaakt`, and not `verleend`.
- **Utrechtse Heuvelrug** `(RX2026-00000497, 2 maart 2026)` in the title → taken as a range
  start. A bare parenthesised publication date with no cue at all.

Both are admin dates leaking past the veto, which is the failure mode that costs a user
something: a wrong date is worse than no date. The ten misses cost only coverage.

## 6. v3 — three layers, and a genuinely independent measurement

Rebuilt as three layers instead of one regex accumulating exceptions:

| layer | does | why separate |
|---|---|---|
| **detect** | every date-like token plus the text either side | no judgement at all, so a cue change cannot alter what is found |
| **classify** | event / admin / correction / unknown, recording the cue | a new municipal phrasing is one row in one table |
| **decide** | which classified candidate to return, or NULL | precedence stated once, not implied by code order |

Three things fixed, one per layer, each found by printing candidates rather than guessing:

- **detect** — a RUN of dates sharing one month/year tail. `14 mei, 11 juli en 12 september
  2026`, `27, 29 en 30 april en 1 mei 2026`. Detecting only the complete trailing date made
  the LAST day the candidate, which is the end and never the start.
- **classify** — an administrative cue may FOLLOW its date. Dutch puts the participle at the
  end of the clause: `heeft op maandag 15 december 2025 een verzoek ONTVANGEN`. Looking only
  backwards read that as an event date and returned the day the request came in, eight months
  early.
- **classify** — the connector between an event noun and its date is not only a preposition.
  `Kermis Den Burg, 8 t/m 12 juli 2026`, `"Zomermarkt, 27-06-2026"`, `Pinksterfietstocht (25
  mei 2026)`. Demanding `op|van` cost 14 of 19 fold-A misses and bought nothing — what keeps
  this honest is the admin veto in front, not a narrow connector.

### Fold C — 30 municipalities used in neither dev, nor A, nor B

| | precision | recall | null rate | hallucination |
|---|---|---|---|---|
| fold A (dev from v3 on) | 100.0% | 73.8% | 29.5% | 0.0% |
| fold B (dev from v3 on) | 92.0% | 79.3% | 16.7% | 0.0% |
| **fold C — sealed** | **100.0%** | **65.5%** | **36.7%** | **0.0%** |

19 of 19 returned dates correct, 10 honest NULLs, 1 correct silence. **Every error is a
miss.** The NULL policy held: nothing was invented and no administrative date was served as an
event date on any of the three folds.

Null rate is printed beside precision on purpose — 100% precision is purchasable with silence,
and 36.7% is the price being paid for it here.

### The three remaining misses are nameable, and fixing them costs fold C

Diagnosed by printing candidates, not by guessing:

- **Veendam** `heeft verleend: Datum evenement: zaterdag 7 maart 2026` → classified admin.
  `verleend` falls inside the 45-character veto window, and the veto outranks the label even
  though `Datum evenement:` sits closer and is far more specific. Precedence bug, not a
  vocabulary gap.
- **Midden-Delfland** `van Koningsdag 27 april 2026` and **Drechterland** `Kermisborrel
  27-6-2026` → unknown. An event NAME followed directly by a date with no connector at all.
  Very common in titles, and currently unreachable because the prose rule requires a
  connector.

Both are one-table changes in layer 2. **Neither is made here**: fold C has now been read, so
any number quoted after a fix needs a fold D. There are 49 unused test municipalities left,
so that is available and cheap.

## 7. Decision owed

Not wired into anything yet. Three options, in the order I would take them:

1. **Print the date only where `basis` is a labelled field.** On fold B the label tiers made no
   wrong calls at all — both errors came from the prose tiers. That trades recall for a
   precision the number can carry, and the endpoint already has the habit of saying how a
   value was derived (`stage` is labelled "from title").
2. **Add `verleend|toegekend|besloten` to the veto and re-cut a fold C.** Cheap, and the honest
   cost is that fold B is now spent — a third fold is needed for any number quoted afterwards.
3. **Leave it and show no date outside Amsterdam.** Correct today and the weakest product.

Not attempted, and worth saying so: an LLM would very likely clear 95% on this task, and the
offline model is already in the project. It was not tried because the regex path was cheap
enough to measure first, not because it was ruled out.
