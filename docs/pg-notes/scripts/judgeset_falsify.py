"""Falsification probes against the +0.194 nDCG@10 vec-over-fts_body claim (2026-11-13).

    .venv/bin/python docs/pg-notes/scripts/judgeset_falsify.py

Reads the same frozen files as scripts/experiments/judgeset_score.py and touches nothing live.
Four probes, each asking whether the reported point estimate and its significance are properties
of the ARMS or of a scoring convention that was never named.

  0  CALIBRATION   reproduce the published 0.437 / 0.475 / 0.669 from this harness first.
                   A re-scorer that cannot hit the published numbers is measuring itself.
  1  TIES          ts_rank_cd emits 2-3 distinct scores over fts_body's top 10 (median 2), so
                   97% of its judged slots sit in an exact-score tie and 41% of the tie groups
                   touching the top 10 mix grades. vec: 0% mixed. Re-resolve each arm's ties
                   randomly / in that arm's favour / against it. The arm's own score vector
                   asserts no order inside a tie, so every one of these is an equally faithful
                   realisation of the arm.
  2  DISTINCT      27% of vec's top-10 slots repeat a body text already on its own page (51 of
                   them graded 2) against 6% for fts_body. Score distinct stored body texts,
                   collapsing the IDEAL the same way, with no unjudged document ever entering
                   any list -- backfilling from top-50 would re-introduce the pool bias the
                   scorer exists to refuse.
  3  MULTIPLICITY  the scorer prints five comparisons and labels three "significant" at an
                   uncorrected 0.05. Holm across the five it prints.
  4  FRAGILITY     drop-one-query jackknife on the bootstrap CI, and the smallest effect this
                   design could ever have called significant.
"""
from __future__ import annotations

import json
import math
import pathlib
import random
import statistics

D = pathlib.Path(__file__).resolve().parents[1]
K = 10
ARMS = ["fts_title", "fts_body", "vec", "hybrid"]
SEED = 20261105

raw = json.loads((D / "judgeset-raw.json").read_text())
judged = json.loads((D / "judgeset-judgments.json").read_text())
sheet = {e["query"]: {c["id"]: c for c in e["candidates"]}
         for e in json.loads((D / "judgeset-judge_sheet.json").read_text())}


def dcg(g):
    return sum((2 ** x - 1) / math.log2(i + 2) for i, x in enumerate(g))


def ndcg(g, pool):
    ideal = dcg(sorted(pool, reverse=True)[:K])
    return dcg(g[:K]) / ideal if ideal else 0.0


def boot(diffs, b=20000, seed=SEED):
    r = random.Random(seed)
    n = len(diffs)
    m = sorted(sum(diffs[r.randrange(n)] for _ in range(n)) / n for _ in range(b))
    return statistics.mean(diffs), m[int(.025 * b)], m[int(.975 * b)]


def ttest_rel_p(diffs):
    """Two-sided paired-t p by numeric integration of the t pdf. No scipy dependency."""
    n = len(diffs)
    t = statistics.mean(diffs) / (statistics.stdev(diffs) / math.sqrt(n))
    v = n - 1
    c = math.gamma((v + 1) / 2) / (math.sqrt(v * math.pi) * math.gamma(v / 2))
    a, steps = abs(t), 200000
    h = 40.0 / steps
    tail = sum(c * (1 + (a + i * h) ** 2 / v) ** (-(v + 1) / 2) * h for i in range(steps))
    return t, 2 * tail


def tie_groups(ids, sc):
    out, cur, last = [], [], None
    for i in ids:
        v = sc[i]
        if last is not None and v != last:
            out.append(cur)
            cur = []
        cur.append(i)
        last = v
    if cur:
        out.append(cur)
    return out


def order(arm, rec, mode, rng):
    """Realise one arm's top-K under a tie-resolution convention."""
    sc = rec["scores"][arm]
    grades = judged[rec["query"]]
    res = []
    for g in tie_groups(rec["arms"][arm], sc):
        if mode == "as_is":
            res += g
        elif mode == "rand":
            h = g[:]
            rng.shuffle(h)
            res += h
        elif mode == "best":
            res += sorted(g, key=lambda i: -grades.get(i, 0))
        elif mode == "worst":
            res += sorted(g, key=lambda i: grades.get(i, 0))
        if len(res) >= K:
            break
    return res[:K]


def score(mode, arms, rng):
    out = {}
    for arm in arms:
        vals = []
        for rec in raw["queries"]:
            grades = judged[rec["query"]]
            top = order(arm, rec, mode, rng)
            vals.append(ndcg([grades.get(i, 0) for i in top], list(grades.values())))
        out[arm] = vals
    return out


SCORED = ["fts_title", "fts_body", "vec"]   # hybrid is RRF and stores no score vector
rng = random.Random(7)

print("PROBE 0 -- calibration against the published table")
base = score("as_is", SCORED, rng)
for a in SCORED:
    print(f"  {a:<10} {statistics.mean(base[a]):.3f}   published "
          f"{{'fts_title': 0.437, 'fts_body': 0.475, 'vec': 0.669}}[{a!r}]")

print("\nPROBE 1 -- exact-score ties")
for arm in SCORED:
    dis = [len({rec['scores'][arm][i] for i in rec['arms'][arm][:K]}) for rec in raw["queries"]]
    ng = mixed = 0
    for rec in raw["queries"]:
        g = judged[rec["query"]]
        seen = 0
        for grp in tie_groups(rec["arms"][arm], rec["scores"][arm]):
            if seen >= K:
                break
            seen += len(grp)
            if len(grp) < 2:
                continue
            ng += 1
            if len({g[i] for i in grp if i in g}) > 1:
                mixed += 1
    print(f"  {arm:<10} distinct scores in top-10: median {statistics.median(dis):.0f} "
          f"(min {min(dis)}, max {max(dis)})   tie groups touching top-10 {ng:3d}, "
          f"mixed-grade {mixed:3d} ({100 * mixed / ng:.0f}%)")
for mode in ["as_is", "best", "worst"]:
    s = score(mode, SCORED, random.Random(7))
    d = [s["vec"][i] - s["fts_body"][i] for i in range(40)]
    m, lo, hi = boot(d)
    print(f"  ties={mode:<6} vec {statistics.mean(s['vec']):.3f}  fts_body "
          f"{statistics.mean(s['fts_body']):.3f}  diff {m:+.3f} CI [{lo:+.3f},{hi:+.3f}]  "
          + ("significant" if lo > 0 or hi < 0 else "INSIDE NOISE"))
gaps = []
for t in range(200):
    s = score("rand", ["fts_body", "vec"], random.Random(1000 + t))
    gaps.append(statistics.mean(s["vec"]) - statistics.mean(s["fts_body"]))
gaps.sort()
print(f"  ties=rand   200 draws: diff {statistics.mean(gaps):+.3f} "
      f"[{gaps[5]:+.3f},{gaps[194]:+.3f}]  (tie-break randomness only, not query sampling)")

print("\nPROBE 2 -- distinct stored body text")


def txt(q, i):
    return (sheet[q][i]["text"] or "").strip().lower()


res, drops = {}, {}
for arm in ARMS:
    vals, dropped, g2 = [], 0, 0
    for rec in raw["queries"]:
        q = rec["query"]
        gr = judged[q]
        seen, keep = set(), []
        for i in rec["arms"][arm][:K]:
            t = txt(q, i)
            if t and t in seen:
                dropped += 1
                g2 += (gr[i] == 2)
                continue
            seen.add(t)
            keep.append(i)
        best = {}
        for i, g in gr.items():
            best[txt(q, i)] = max(best.get(txt(q, i), -1), g)
        ideal = dcg(sorted(best.values(), reverse=True)[:K])
        vals.append(dcg([gr[i] for i in keep]) / ideal if ideal else 0.0)
    res[arm] = vals
    drops[arm] = (dropped, g2)
    print(f"  {arm:<10} repeated-text slots dropped {dropped:3d}/400 ({g2} of them grade 2)   "
          f"distinct-text nDCG@10 {statistics.mean(vals):.3f}")
for a, b in [("vec", "fts_body"), ("vec", "fts_title")]:
    d = [res[a][i] - res[b][i] for i in range(40)]
    m, lo, hi = boot(d)
    _, p = ttest_rel_p(d)
    print(f"  {a:<7}-{b:<9} {m:+.3f} CI [{lo:+.3f},{hi:+.3f}] p={p:.4f}  "
          + ("significant" if lo > 0 or hi < 0 else "INSIDE NOISE"))

print("\nPROBE 3 -- multiplicity over the five comparisons the scorer prints")
pairs = [("vec", "fts_body"), ("vec", "fts_title"), ("hybrid", "vec"),
         ("hybrid", "fts_body"), ("fts_body", "fts_title")]
sc0 = json.loads((D / "judgeset-scores.json").read_text())
rows = []
for a, b in pairs:
    d = [sc0[q][a]["ndcg"] - sc0[q][b]["ndcg"] for q in sc0]
    rows.append((a, b, statistics.mean(d), ttest_rel_p(d)[1]))
for rank, (a, b, m, p) in enumerate(sorted(rows, key=lambda r: r[3])):
    thr = 0.05 / (len(rows) - rank)
    print(f"  {a:<9}-{b:<9} {m:+.3f} p={p:.4f}  Holm thr {thr:.4f} -> "
          + ("pass" if p < thr else "FAIL"))

print("\nPROBE 4 -- fragility of the published interval")
d = [sc0[q]["vec"]["ndcg"] - sc0[q]["fts_body"]["ndcg"] for q in sc0]
m, lo, hi = boot(d)
sd = statistics.stdev(d)
se = sd / math.sqrt(len(d))
t, p = ttest_rel_p(d)
print(f"  as published: {m:+.3f} CI [{lo:+.3f},{hi:+.3f}]  t={t:.3f} p={p:.4f} sd={sd:.3f}")
flips = [q for i, q in enumerate(sc0)
         if not ((lambda r: r[1] > 0 or r[2] < 0)(boot(d[:i] + d[i + 1:])))]
print(f"  drop-one-query jackknife: {len(flips)} of 40 removals put 0 inside the CI")
print(f"  smallest effect this design could declare significant = t.975*se = {2.0227 * se:.3f}; "
      f"observed {m:+.3f} is {100 * m / (2.0227 * se):.0f}% of it")
print(f"  MDE at power .80 = 2.80*sd/sqrt(40) = {2.8 * sd / math.sqrt(40):.3f}")
