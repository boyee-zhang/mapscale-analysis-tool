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
