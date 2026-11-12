# A judged relevance set for semantic search over `bekendmaking`

Built 2026-11-05 against the live national table (94,183 publications, 294 municipalities, 2026).
Harness `scripts/experiments/judgeset_pool.py` (pooling) and `judgeset_score.py` (metrics); data
in `judgeset-{raw,judge_sheet,judgments}.json` and `judgeset-vocab.txt`.

## Why this exists

`semantic-search.md` shipped a working vector search and measured its *speed* and its *index
recall*, and said plainly that retrieval **quality** was unmeasured. There was a judged set
already — `semsearch-ab-judgments.json`, 15 queries, 519 documents — but it cannot be reused for
this comparison, and the reason is recorded in `semsearch-ab.md` in the project's own words:

> It returned 93 documents that were never in the pool, so scoring it against the old pool
> counts them as irrelevant by default — a guaranteed underestimate.

That pool was built from FTS arms only. **Measured here: the FTS arms and the vector arm share
0.1 documents out of their top 10.** Scoring the vector arm against the old pool would have
counted roughly 99% of its results as irrelevant by construction. So the pool is rebuilt with
the vector arm inside it.

## The rubric, written before any candidate was seen

Graded, not binary — `0` / `1` / `2`. The whole claim being tested is that the embedding ranks
*better within a topic*, and a binary label cannot see the difference between "exactly this" and
"vaguely adjacent", which is the only place that claim lives.

| grade | meaning |
|---|---|
| **2** | The publication's subject **is** the thing asked about. A resident who asked this would act on it. |
| **1** | Adjacent: the same subject area, but not the thing asked — or the right subject carried only by a procedural shell with no description of what is actually happening. |
| **0** | A different subject. |

Decided in advance, because each of these recurs and deciding it per document is how a judge's
bias gets in:

- **Stage is irrelevant to the grade.** An application, a draft decision and a granted permit for
  the asked-about thing are all `2`. The corpus publishes every stage and the question never asks
  for one.
- **Place is irrelevant.** This is the corpus-wide mode, which has no spatial filter; a
  publication from a different municipality is graded on its subject like any other.
- **Date is irrelevant.** The window is the whole of 2026 for every arm.
- **Scale cuts both ways and is graded per question.** For `砍树` (any tree felling) a single-tree
  permit is `2`. For `砍掉一整片树林` (clearing woodland) a single-tree permit is `1`.
- **A template row with no description is graded on its title alone.** If the title names the
  right subject it is `2`; if the title names no subject at all it is `0`, not `1`. "Might be
  relevant if I could see inside it" is not partial relevance, and grading it `1` would inflate
  every arm that favours templated rows.
- **For the noise questions the concept is audible disturbance**, not the permit family: physical
  works are `2`, a permit that merely accompanies works (fire safety, zoning) is `1`, and a permit
  with no physical activity (holiday-let registration, an alcohol licence) is `0` even where the
  venue is plainly noisy — otherwise the question becomes "is this place busy", which is a
  different question and one nothing in the corpus answers.

### Blinding

The sheet is the pooled union of all four arms, deduplicated, shuffled with a fixed seed, and
stripped of arm, rank and score. Position on the sheet carries no information. Every grade is
recorded per `(query, document)` so it can be reviewed, overridden, or reused when the query set
grows.

### Validity threat, stated in advance and unresolved

**I built the systems and I am the judge.** That is the same threat `semsearch-ab.md` recorded,
and the two mitigations added since are the blinding above and this rubric being fixed before the
first candidate was read. Neither makes the judge independent. What the rubric does buy is that a
disagreement is now about a *stated rule* rather than about a feeling, so a reader who rejects a
rule can re-score from the recorded grades without re-judging.

### One rule added during judging

`intrekking` / withdrawn applications get **1**, not 2. The pre-registered stage rule names
application, draft and granted; a cancellation is a fourth state, where the answer to "is this
happening" is "no". Recorded here rather than applied quietly, and applied uniformly to all of
them.

---

## The set

| | |
|---|---|
| queries | **40** — the 15 from `semsearch-ab.md` plus 25 new, mostly on the event side of the corpus |
| arms pooled | 4, at depth 10 each |
| judged pairs | **1,085** (27.1 per query, 10–33) |
| grades | 436 × `2`, 235 × `1`, 414 × `0` |
| corpus | 94,183 publications, 294 municipalities, 2026 |

The four arms, all over the same window, all taking their own top 10:

| arm | what it is |
|---|---|
| `fts_title` | Dutch FTS + `ts_rank_cd` over `title \|\| abstract`, LLM-expanded terms + optional `doc_type`/`activiteit` filters — the configuration measured best of five in `semsearch-ab.md` |
| `fts_body` | the SAME LLM terms and filters, over `bekendmaking_search_text.activity_text` — the text that was embedded |
| `vec` | the production path: `query: ` + the question through multilingual-e5-base, cosine over `bekendmaking_embedding` |
| `hybrid` | reciprocal rank fusion of `fts_body` and `vec`, k=60, over each arm's top 50 |

Two decisions inside that table are load-bearing:

**The vocabulary in the expansion prompt is measured at run time, not pasted.** The prompt that
won the earlier A/B was shown a vocabulary from a 61,853-row, three-municipality corpus. That
corpus has moved: `evenementenvergunning` went from minor to largest type (37%), and
`TVM parkeervak` — then the top abstract prefix at 12,301 rows — is an Amsterdam template that no
longer leads. A stale vocabulary would handicap the baseline, and a handicapped baseline is not
one. Current dump: `judgeset-vocab.txt`.

**The vector arm is scored with the HNSW index OFF, as an exact scan, and the script checks the
plan.** This set measures the retrieval model; how much of its ranking survives an approximate
scan is a separate, already-measured question (`semantic-search.md`: recall@20 was 72.5% at the
default `ef_search`). Folding the index's loss into the model's score would understate the model.
It also could not have run: pgvector returns at most `ef_search` candidates and the default 40 is
below the fusion depth of 50.

## Why the old judgments could not be reused — measured

Mean overlap between arms' top 10, over 40 queries:

| | `fts_title` | `fts_body` | `vec` |
|---|---|---|---|
| `fts_body` | 2.5 / 10 | | |
| `vec` | **0.1 / 10** | **0.1 / 10** | |
| `hybrid` | 1.3 / 10 | 4.8 / 10 | 5.0 / 10 |

**FTS and the embedding return almost disjoint document sets.** Scored against the old FTS-only
pool, roughly 99% of the vector arm's results would have been counted irrelevant by construction.
That is not a small correction to the earlier numbers; it is the reason they could not be used.

The 2.5/10 on the first row is the other half of the same point: changing only *which text* the
same terms are matched against replaces three quarters of the result list. That is a real axis,
and it is why `fts_body` exists.

## Result

| arm | nDCG@10 | P@10 | P@10 (grade 2 only) | Recall@10 | Recall@10 (grade 2) | empty |
|---|---|---|---|---|---|---|
| `fts_title` | 0.437 | 0.560 | 0.343 | 0.271 | 0.272 | 1 |
| `fts_body` | 0.475 | 0.560 | 0.362 | 0.327 | 0.361 | 1 |
| **`vec`** | **0.669** | **0.695** | **0.458** | **0.482** | **0.471** | 0 |
| `hybrid` | 0.606 | 0.652 | 0.427 | 0.432 | 0.435 | 0 |

Recall is over each query's pooled relevant set, so it compares arms and does **not** estimate
what the corpus still hides — a document no arm surfaced is in nobody's pool.

Paired bootstrap on the 40 per-query nDCG@10 differences, 20,000 resamples:

| comparison | mean | 95% CI | | per-query |
|---|---|---|---|---|
| `vec` − `fts_body` | **+0.194** | **[+0.024, +0.361]** | **significant** | 25 W / 13 L / 2 T |
| `vec` − `fts_title` | **+0.232** | **[+0.057, +0.397]** | **significant** | 24 W / 10 L / 6 T |
| `hybrid` − `fts_body` | +0.131 | [+0.041, +0.221] | significant | 25 W / 13 L / 2 T |
| `hybrid` − `vec` | −0.063 | [−0.150, +0.025] | inside noise | 15 W / 20 L / 5 T |
| `fts_body` − `fts_title` | +0.037 | [−0.044, +0.121] | inside noise | 18 W / 14 L / 8 T |

### Three readings, in order of how much they change

**1. The embedding wins on ranking, and the text is not the explanation.** This is what the
`fts_body` arm was built to establish. Matching the same LLM terms against the embedded text
instead of `title || abstract` is worth +0.037 nDCG and the interval crosses zero. So the +0.194
is not the source text and not the applicant's description — it is the ranking function. That is
the hypothesis `semsearch-ab.md` left open ("embedding fixes ranking — substantially
strengthened, not confirmed"), now measured on a pool that is not biased against it.

**2. The significance is real and the +0.194 is probably too big.** Stated plainly because it is
easy to quote the number and drop this: with the measured sd of paired differences (0.543), 40
queries have 80% power only for an effect of **0.241**, and the observed effect is smaller than
that. A significant result from an underpowered test is one that had to land on the lucky side of
its own noise, so the point estimate is inflated even where the sign is sound. What is well
supported is the **direction** (25 wins to 13) and that the true effect is above zero. Resolving
the magnitude to ±0.05 would need about **930 queries** — the same wall the factorial experiment
hit, one metric further along.

**3. Equal-weight RRF made things worse, and the per-query table says why.** Fusion is the
reflex here and it lost 0.063 nDCG to pure vector (20 queries worse, 15 better). The arms are
strongly complementary — `vec` scores 0.000 where `fts_body` scores 0.710 (demolition), 0.120
against 1.000 (disabled parking), 0.028 against 1.000 (soil contamination); `fts_body` scores
0.000 where `vec` scores 0.990 (night works), 0.979 (noise rules), 1.000 (Christmas markets) —
which is the textbook condition for fusion to help. It does not, because the arms are not of
equal quality: weighting them equally lets the weaker one pull down the queries where the
embedding is already perfect. The honest next step is a weight or a router, and **this set is the
instrument for it** — with the caveat that re-weighting surfaces documents from outside the
depth-10 pool, so it costs a pool extension and another judging pass, not a re-score.

### Where both arms fail completely

Three queries have **no grade-2 document anywhere in a 4-arm, depth-10 pool**:

| query | pool | what exists |
|---|---|---|
| a skip/container at the roadside | 21 judged, 0 × `2` | `Melding objecten … plaatsen van een container op de openbare weg` is in the corpus — it appears in another query's pool |
| rules on keeping or walking dogs | 22 judged, 0 × `2` | plausibly absent: dog rules are municipal bylaws (`algemeen verbindend voorschrift`), a publication family this corpus does not import |
| a plan open for residents' input | 10 judged, 0 × `2` | `participatie` is 22 rows, and the `ontwerpbesluit` / `ter inzage` family is large. **Both FTS arms returned literally nothing** |

The first is the same failure `semsearch-ab.md` recorded in 2026-11-01 and it has survived both a
facet-strip fix and the switch to embeddings — three different retrieval methods, one document
family, still unreachable. The third is worse than a ranking failure: the LLM's terms matched no
document at all, so the query had no FTS result to rank.

Set against that, the `maatwerkvoorschrift … geluidhinder werktijden` family — which
`semsearch-ab.md` flagged as "literally the subject the noisy-works layer wants, and no query in
either experiment surfaced it" — is now retrieved, by the vector arm, at nDCG 0.979.

## Scale of validity

40 queries, all Chinese, 94,183 publications, 2026 only, one embedding model
(`multilingual-e5-base`), one judge. The numbers are comparisons between these four arms on this
corpus; they are not an absolute quality figure and they will move with the corpus. What should
survive a corpus change is the ordering, the near-zero overlap, and the `fts_body` control — the
first two because they are properties of the methods and the third because it is a null result.

---

# Using the set: where to stop returning results (2026-11-05)

A nearest-neighbour query always returns `limit` rows. It has no notion of "nothing here
matches", so inside a 15-minute walk that holds three music events, ranks 4-30 come back anyway
and the answer reads as broken although the ranking was right. The judged set is the instrument
for deciding where to stop, and it changed the answer twice.

## Four rule families, measured on the vec arm's judged top-10 (400 rows, 278 relevant)

At matched operating points:

| rule | kept | P@kept | relevant lost | queries left with nothing |
|---|---|---|---|---|
| no cut (what shipped first) | 400 | 0.695 | 0 | 0 |
| `sim >= top − δ` (δ = .005–.030) | 215–400 | **0.688–0.694** | up to 76 | 0 |
| cut at the first gap > .004–.012 | 289–391 | **0.684–0.692** | up to 48 | 0 |
| `sim >= 0.810` absolute | 340 | 0.732 | 29 | 2 |
| `sim − background >= 0.050` | 369 | **0.740** | **5** | **1** |
| `(sim − background) / sd >= 2.5` | 387 | 0.703 | 6 | 1 |

**The two reflex rules are worthless.** "Within δ of the top" and "cut at the biggest gap" both
leave precision where no cut at all leaves it, because they discard relevant and irrelevant rows
in the same proportion. The score ordering is noisy, so a gap is not a relevance boundary. This
is the kind of rule that looks principled and measures as nothing.

## Correction 1: an absolute floor is language-biased

The first version shipped was `similarity >= 0.810`, and it is wrong in a way the judged set
could not show, because every query in the set is Chinese. The same question in three languages,
same isochrone:

| query | top similarity | verdict at 0.810 |
|---|---|---|
| `muziekfestival` | 0.850 | kept |
| `music festival` | 0.822 | kept |
| `音乐节` | 0.782 | **cut — and its top hit was a correct Hofjesconcert** |

The ranking is right in all three. What moves is the absolute scale: multilingual-e5 places a
cross-language pair further apart than a same-language pair about the same thing. A single
constant therefore makes the cut strictest for whoever is not writing Dutch.

The fix is to measure the offset rather than assume it away: subtract the query's mean similarity
to the population being searched. That baseline moves **0.731–0.780** across those queries — a
0.050 spread, the same size as the threshold itself — and it tracks the language exactly (Dutch
0.753–0.780, English 0.742–0.757, Chinese 0.731–0.767).

## Correction 2: normalising by the spread makes it worse

Centring corrects the location but not the scale, and cross-language matching compresses the
whole distribution, so a z-score was the obvious next move. Measured, it loses: P 0.703 against
the margin's 0.740 at matched loss, and 66 relevant rows lost against 38 at matched precision.
The background sd moves 0.013–0.030 across queries and that movement is not aligned with
relevance. **The plain margin wins**; the better-motivated rule was the worse one.

## The baseline is the population searched, and it is not free

Within a shape the baseline is the mean over the documents inside that shape; corpus-wide it is
the mean over a fixed `hashtext` sample (3,090 rows across 227 of 294 municipalities). Measured
over 6 queries × 2 cities, the local and corpus baselines differ by −0.009 to **+0.025** — half
the threshold — so a corpus baseline near Utrecht makes a felling query look 0.025 more relevant
than it is, which lets padding through in exactly the direction the cut exists to stop. The price
is a second pass over the filtered set: **120–350 ms warm** (846 ms cold; discard that one).
One pass would do both, via a `scored` CTE referenced twice, but that rewrites the SQL whose plan
shape the `hnsw.iterative_scan` guard depends on, and that guard was bought with a zero-rows bug.

## What shipped

`RELEVANCE_MARGIN = 0.050` over the per-query baseline. The response carries `strong` (how many
cleared it) and `background`, and **drops nothing** — the UI shows the answers and keeps the tail
behind "Show N weaker matches", off the map. Three reasons nothing is dropped: the margin is
calibrated corpus-wide and only carried over to the spatial mode; a short cross-language query in
a small isochrone can still come back with nothing offered (`音乐节` sits at margin 0.038 with a
correct result on top); and server-side truncation would make "nothing matched well"
indistinguishable from "the search broke".

Verified in the browser: `muziekfestival` at the default centre now offers **2 matches** with
their event dates instead of 30 rows, and `braderie of rommelmarkt` says "Nothing here matches …
well" with 30 weaker matches behind a click, where it previously listed 30 night-work exemptions.

## Follow-up: the contract was wrong, and one real miss (2026-11-05)

Reported from use, searching `braderie` in a 10-minute walk of Diemen over three months: the
endpoint returned 22 items and the panel showed none. Two separate things, and only one of them
was a defect.

**The suppression was correct.** Checked every returned row for `braderie|markt|rommel|kraam|
vlooien|evenement`: not one hit. The 22 were tree felling, a wall opening, window stickers and
camera-surveillance designations. There is no braderie in that isochrone in that window, and
offering none is the right answer.

**The contract was the defect.** `items` carried all 22 with a `strong: 0` count saying where to
cut, so "what the endpoint returns" and "what a reader is shown" were different lists and every
consumer had to re-derive the split. Fixed: `items` is the answers, `weaker` is the near-misses,
`total` counts the answers. The tail is still served so the disclosure costs no second request,
and `background: null` still distinguishes "could not measure the bar" from "nothing cleared it".

**A measured non-problem, recorded so it is not re-investigated.** The baseline in spatial mode is
computed over the documents in the shape, and in this case that is only 64 (three months) or 178
(twelve) — small enough to suspect a noisy mean. Measured against the corpus baseline for two
queries × two windows: the difference is **±0.006**. It is stable; no fallback is needed.

### The real miss, and it is the threshold's fault

`muziekfestival` in the same isochrone returns **nothing**, and the top near-miss is:

> *Aanvraag evenementenvergunning — Upload_Wintermarkt in Betondorp 2026 — "Luisteren naar
> muziek, handgemaakte producten kopen"*

A market, with live music. Margin **0.0491** against a threshold of 0.050 — cut by 0.0009 — and
the drop to the next row is 0.0491 → 0.0192, so there is a clean cliff with exactly one answer
above it. That document is also the right answer for `braderie`.

The cause is the scale this was calibrated on. The judged set is corpus-wide, where a query's
top-10 are the best documents in the country and margins run large — so large that every
threshold from 0.030 to 0.045 cuts **nothing at all** there (400/400 kept, P 0.695 unchanged) and
0.050 is the first that bites. Inside one isochrone the best available document is weaker in
absolute terms, so real answers land near 0.05 rather than well above it, and a threshold chosen
where corpus-mode margins are comfortable sits right on top of the spatial mode's answers.

Lowering the constant is not the fix: 0.045 measures strictly worse corpus-wide (P 0.698 against
0.740, i.e. barely cutting). **The two modes need two values, and the spatial one cannot be
calibrated from this set** — it needs its own judged set, built from isochrone searches. Roughly
15-20 queries at depth 10 would do it, since the population per query is 64-178 rather than 94,183.

Until then the behaviour is: conservative in the spatial mode, with the near-misses one click
away. Stated rather than hidden, because "no results" for a query that has one is the failure a
reader cannot see.
