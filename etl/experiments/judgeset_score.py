"""Score the four retrieval arms against the judged relevance set.

    .venv/bin/python scripts/experiments/judgeset_score.py

Reads `judgeset-raw.json` (what each arm returned) and `judgeset-judgments.json` (grades keyed by
document identifier, not by sheet position -- positions are an artefact of the blind sheet and
would silently break if the sheet were ever reshuffled).

METRICS, AND WHY EACH ONE.

  nDCG@10       The headline. Graded, so it can see the difference between "exactly this" and
                "vaguely adjacent", which is the entire claim being tested: the earlier
                experiments established that the loss is RANKING, not reach, and a binary metric
                cannot measure ranking inside a topic. Gain is 2^g - 1 (0 / 1 / 3).
  P@10          Binary, grade >= 1, for continuity with the 70.0% figure in semsearch-ab.md.
  P@10 strict   Binary, grade == 2 only. The pair matters: an arm that wins on P@10 and loses on
                strict is filling the page with adjacent documents.
  Recall@10     Over the POOLED relevant set for that query. An upper bound in the optimistic
                direction and a floor in the pessimistic one: documents no arm surfaced are in
                nobody's pool, so no arm is credited or charged for them. It compares arms; it
                does NOT estimate how much of the corpus was missed.

EVERY ARM'S TOP-10 IS FULLY JUDGED BY CONSTRUCTION -- the pool is the depth-10 union of all four
arms -- and the script ASSERTS that rather than trusting it. An unjudged document scored as
irrelevant is precisely the pool-bias failure this set exists to remove, and it would be
invisible otherwise.
"""
from __future__ import annotations

import json
import math
import pathlib
import random
import statistics

OUT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "pg-notes"
ARMS = ["fts_title", "fts_body", "vec", "hybrid"]
K = 10
BOOTSTRAP = 20000
SEED = 20261105


def dcg(grades: list[int]) -> float:
    return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(grades))


def ndcg(grades: list[int], all_grades: list[int]) -> float:
    ideal = dcg(sorted(all_grades, reverse=True)[:K])
    return dcg(grades[:K]) / ideal if ideal else 0.0


def main() -> None:
    raw = json.loads((OUT / "judgeset-raw.json").read_text())
    judged = json.loads((OUT / "judgeset-judgments.json").read_text())
    records = raw["queries"]

    per_query: dict[str, dict[str, dict]] = {}
    unjudged = []
    for rec in records:
        q = rec["query"]
        grades = judged[q]
        pool = list(grades.values())
        rel = sum(1 for g in pool if g >= 1)
        rel2 = sum(1 for g in pool if g == 2)
        per_query[q] = {}
        for arm in ARMS:
            top = rec["arms"][arm][:K]
            missing = [i for i in top if i not in grades]
            if missing:
                unjudged.append((q, arm, missing))
            g = [grades.get(i, 0) for i in top]
            per_query[q][arm] = {
                "ndcg": ndcg(g, pool),
                "p": sum(1 for x in g if x >= 1) / K,
                "p_strict": sum(1 for x in g if x == 2) / K,
                "recall": (sum(1 for x in g if x >= 1) / rel) if rel else float("nan"),
                "recall2": (sum(1 for x in g if x == 2) / rel2) if rel2 else float("nan"),
                "returned": len(top),
            }

    if unjudged:
        for q, arm, miss in unjudged:
            print(f"UNJUDGED {arm} on {q}: {miss}")
        raise SystemExit("refusing to score: an arm returned documents that were never judged")

    print(f"{len(per_query)} queries, "
          f"{sum(len(v) for v in judged.values())} judged (query, document) pairs\n")

    print(f"{'arm':<11} {'nDCG@10':>8} {'P@10':>7} {'P@10=2':>7} {'Rec@10':>7} "
          f"{'Rec@10=2':>9} {'empty':>6}")
    for arm in ARMS:
        rows = [per_query[q][arm] for q in per_query]
        fin = lambda key: [r[key] for r in rows if not math.isnan(r[key])]  # noqa: E731
        print(f"{arm:<11} {statistics.mean(r['ndcg'] for r in rows):8.3f} "
              f"{statistics.mean(r['p'] for r in rows):7.3f} "
              f"{statistics.mean(r['p_strict'] for r in rows):7.3f} "
              f"{statistics.mean(fin('recall')):7.3f} "
              f"{statistics.mean(fin('recall2')):9.3f} "
              f"{sum(1 for r in rows if r['returned'] == 0):6d}")

    # Paired bootstrap over the per-query nDCG differences. Paired because the same 40 queries go
    # to every arm, so the query-to-query variance -- which is large, see the per-query table --
    # cancels. An unpaired interval here would be mostly noise about which questions are easy.
    rng = random.Random(SEED)
    queries = list(per_query)
    print(f"\npaired bootstrap on nDCG@10, {BOOTSTRAP} resamples, 95% CI")
    pairs = [("vec", "fts_body"), ("vec", "fts_title"),
             ("hybrid", "vec"), ("hybrid", "fts_body"), ("fts_body", "fts_title")]
    for a, b in pairs:
        diffs = [per_query[q][a]["ndcg"] - per_query[q][b]["ndcg"] for q in queries]
        means = []
        for _ in range(BOOTSTRAP):
            sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
            means.append(sum(sample) / len(sample))
        means.sort()
        lo, hi = means[int(.025 * BOOTSTRAP)], means[int(.975 * BOOTSTRAP)]
        wins = sum(1 for d in diffs if d > 1e-9)
        losses = sum(1 for d in diffs if d < -1e-9)
        mark = "significant" if lo > 0 or hi < 0 else "inside noise"
        print(f"  {a:<9} - {b:<9} {statistics.mean(diffs):+.3f}  "
              f"[{lo:+.3f}, {hi:+.3f}]  {mark:<12} "
              f"(wins {wins}, losses {losses}, ties {len(diffs) - wins - losses})")

    # What this set can and cannot resolve. The factorial follow-up in semsearch-ab.md computed
    # this for the 15-query set and the answer -- 79 queries for a 17-point effect -- is why this
    # one is 40. Printing it with the results keeps the next reader from treating any gap as real.
    print("\nwhat 40 queries can resolve (paired t, alpha .05, power .80)")
    for a, b in pairs[:3]:
        diffs = [per_query[q][a]["ndcg"] - per_query[q][b]["ndcg"] for q in queries]
        sd = statistics.stdev(diffs)
        mde = 2.8 * sd / math.sqrt(len(diffs))
        print(f"  {a:<9} vs {b:<9} sd of paired diffs {sd:.3f} -> "
              f"minimum detectable effect {mde:.3f} nDCG "
              f"(n for 0.05: {math.ceil((2.8 * sd / 0.05) ** 2)})")

    print("\nper query (nDCG@10)")
    print(f"{'query':<26} " + " ".join(f"{a:>10}" for a in ARMS) + "   rel/pool")
    for q in queries:
        rels = sum(1 for g in judged[q].values() if g >= 1)
        print(f"{q:<26} " + " ".join(f"{per_query[q][a]['ndcg']:10.3f}" for a in ARMS)
              + f"   {rels:3d}/{len(judged[q]):3d}")

    (OUT / "judgeset-scores.json").write_text(json.dumps(per_query, ensure_ascii=False, indent=1))
    print(f"\nwrote judgeset-scores.json to {OUT}")


if __name__ == "__main__":
    main()
