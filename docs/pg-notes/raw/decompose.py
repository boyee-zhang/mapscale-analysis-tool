"""Is the loss ORDERING or the CANDIDATE SET? Decompose nDCG@10 on the frozen judgeset.

    .venv/bin/python <this file>

Reads only docs/pg-notes/judgeset-raw.json (each arm's ranking, depth 50; hybrid 100) and
judgeset-judgments.json (grades by document identifier). Writes nothing. No new judging: every
number below is a rearrangement of grades that already exist.

THE DECOMPOSITION. For one arm and one query, with `ideal` = DCG of the pool's best 10 grades:

  actual   = nDCG@10 of the arm's own top-10 in the arm's own order
  oracle_d = nDCG@10 of the BEST 10 the arm has within its top-d, perfectly ordered
  1.0      = the pool's best 10, perfectly ordered

  oracle_10 - actual   ORDERING headroom: what any reranker over the current 10 can win.
  1.0 - oracle_10      SET headroom: what no reranker over those 10 can reach.
  oracle_50 - oracle_10  what DEEPENING the candidate list buys a perfect reranker.

TWO BIASES, BOTH STATED AND BOTH IN THE SAFE DIRECTION FOR THE HEADLINE.
  * The pool is the depth-10 UNION of the four arms (judgeset_pool.py, POOL_DEPTH = 10), so a
    document at rank 11-50 that no arm put in its top 10 was never judged and counts here as
    grade 0. That makes R@20/R@50 and oracle_20/oracle_50 LOWER bounds.
  * For the same reason the HYBRID row is partly tautological and must not be quoted: hybrid is
    RRF over fts_body and vec, i.e. over the two arms that built most of the pool, so "hybrid
    recovers the pool at depth 20" is close to a restatement of how the pool was made. The
    conclusion below is about the `vec` arm, which is the production path.
"""
from __future__ import annotations

import json
import math
import pathlib
import statistics

OUT = pathlib.Path("/Users/zby/dev/mapscale-analysis-tool/docs/pg-notes")
K = 10
ARMS = ["fts_title", "fts_body", "vec", "hybrid"]
DEPTHS = (10, 20, 50)


def dcg(grades) -> float:
    return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(grades))


def main() -> None:
    raw = json.loads((OUT / "judgeset-raw.json").read_text())
    jd = json.loads((OUT / "judgeset-judgments.json").read_text())
    print(f"pool_depth={raw['pool_depth']} fuse_depth={raw['fuse_depth']} "
          f"queries={len(raw['queries'])}")
    print(f"list length per arm: "
          f"{ {a: len(raw['queries'][0]['arms'][a]) for a in ARMS} }\n")

    print(f"{'arm':<10} {'actual':>7} {'orc@10':>7} {'order':>7} {'set':>7} {'ratio':>6} | "
          + " ".join(f"{'R@' + str(d):>6}" for d in DEPTHS) + " | "
          + " ".join(f"{'orc@' + str(d):>7}" for d in DEPTHS))
    for a in ARMS:
        act, orc, rec = [], {d: [] for d in DEPTHS}, {d: [] for d in DEPTHS}
        for q in raw["queries"]:
            g = jd[q["query"]]
            pool = list(g.values())
            ideal = dcg(sorted(pool, reverse=True)[:K])
            nrel = sum(1 for x in pool if x >= 1)
            full = q["arms"][a]
            act.append(dcg([g.get(i, 0) for i in full[:K]]) / ideal if ideal else 0.0)
            for d in DEPTHS:
                cand = full[:d]
                grades = sorted((g.get(i, 0) for i in cand), reverse=True)[:K]
                orc[d].append(dcg(grades) / ideal if ideal else 0.0)
                if nrel:
                    rec[d].append(sum(1 for i in cand if g.get(i, 0) >= 1) / nrel)
        A, O10 = statistics.mean(act), statistics.mean(orc[10])
        order, setgap = O10 - A, 1.0 - O10
        print(f"{a:<10} {A:7.3f} {O10:7.3f} {order:7.3f} {setgap:7.3f} "
              f"{setgap / order if order else float('nan'):6.1f} | "
              + " ".join(f"{statistics.mean(rec[d]):6.3f}" for d in DEPTHS) + " | "
              + " ".join(f"{statistics.mean(orc[d]):7.3f}" for d in DEPTHS))

    # Per-query paired spread on the vec arm, so the ratio is not read as a point fact.
    print("\nvec, per query: ordering headroom vs set headroom")
    o, s = [], []
    for q in raw["queries"]:
        g = jd[q["query"]]
        pool = list(g.values())
        ideal = dcg(sorted(pool, reverse=True)[:K])
        if not ideal:
            continue
        top = q["arms"]["vec"][:K]
        gr = [g.get(i, 0) for i in top]
        a_ = dcg(gr) / ideal
        o10 = dcg(sorted(gr, reverse=True)) / ideal
        o.append(o10 - a_)
        s.append(1.0 - o10)
    print(f"  ordering: mean {statistics.mean(o):+.3f} median {statistics.median(o):+.3f} "
          f"max {max(o):+.3f}  zero on {sum(1 for x in o if x < 1e-9)}/{len(o)} queries")
    print(f"  set     : mean {statistics.mean(s):+.3f} median {statistics.median(s):+.3f} "
          f"max {max(s):+.3f}  zero on {sum(1 for x in s if x < 1e-9)}/{len(s)} queries")
    print(f"  set > ordering on {sum(1 for x, y in zip(s, o) if x > y)}/{len(o)} queries")

    # A control that can fail: a perfect reorder of the pool's own best 10 must score 1.000,
    # and the arm's own order must never beat its own oracle.
    bad = 0
    for q in raw["queries"]:
        g = jd[q["query"]]
        pool = sorted(g.values(), reverse=True)
        ideal = dcg(pool[:K])
        if ideal and abs(dcg(pool[:K]) / ideal - 1.0) > 1e-9:
            bad += 1
        for a in ARMS:
            top = q["arms"][a][:K]
            gr = [g.get(i, 0) for i in top]
            if ideal and dcg(gr) / ideal > dcg(sorted(gr, reverse=True)) / ideal + 1e-9:
                bad += 1
    print(f"\ncontrols: {'ok' if bad == 0 else str(bad) + ' FAILURES'} "
          f"(ideal normalises to 1.000; no arm beats its own perfect reorder)")


if __name__ == "__main__":
    main()
