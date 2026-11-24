"""Score the body-vs-summary representation arms. Reads body-repr-ab-raw.json, writes nothing.

    .venv/bin/python -m etl.experiments.body_repr_score

TWO ENDPOINTS, AND THE DIFFERENCE BETWEEN THEM IS THE POINT.

  POOL      nDCG@10 over that query's judged pool alone. Every ranked document carries a grade,
            so there is no fill convention, no bound and no pool bias. This is the primary
            endpoint and the only fully defensible one.
  MINI      nDCG@10 over the pool PLUS ~2,000 shared random corpus documents, presumed grade 0.
            **WITHDRAWN AS AN ENDPOINT, 2026-11-18, AND THE WITHDRAWAL IS THE FINDING.** The
            presumption was argued to be conservative -- "a distractor that is really relevant
            can only penalise the arm that ranks it highly" -- which is true and is exactly why
            the metric is invalid: it penalises an arm precisely for doing its job. Inspected
            the moment the number came out at -0.372: of the distractors the body arm put in its
            top-10 for the first three queries, the ones for "is someone felling trees near me"
            are `het kappen en herplanten van twee/vier bomen` (felling and replanting two/four
            trees), for "what noisy works are nearby" it is `ontheffing geluidshinder` (a
            noise-nuisance exemption), and for "will my parking space be taken" they are
            `het aanleggen van een gereserveerde gehandicaptenparkeerplaats`. Those are correct
            answers that are not in the pool -- which is the recall win being looked for, scored
            as zero and reported as harm. This is the pool-bias failure the judgeset exists to
            remove, rebuilt by hand inside the instrument meant to avoid it.

            What survives is the DESCRIPTIVE half, and only descriptively: both arms face the
            same distractors, and act promotes 0.10 of them into its top-10 against body's 3.77.
            That asymmetry is a fact about the two representations. Whether it is a gain or a
            loss cannot be read off this run; it requires judging those documents.

Both normalise against the SAME ideal (the pool's best 10 grades), so MINI can only be lower
than POOL -- which is the MONOTONE control below, and also why MINI cannot express a gain.

WHAT THIS MEASURES. Ordering of a fixed set, under two document representations. It is a
NECESSARY condition for the body representation to be worth a ~5-hour encode: an arm that
cannot order a 27-document enriched pool better will not order 94,183 better. It is NOT
sufficient -- it cannot show that the body arm surfaces anything from outside the pool, which
is the actual recall win, and that needs the judgeset extended.

CONTROLS THAT CAN FAIL. Written because two falsifier verdicts in this work died on controls
that were algebraic identities: `controls: ok` kept printing on a sabotaged input because
`ideal/ideal == 1` and `dcg(x) <= dcg(sorted(x))` have no failing input at all. Each control
here is a transformation of the DATA whose effect on the headline is asserted.
"""
from __future__ import annotations

import json
import math
import pathlib
import random
import statistics

from .judgeset_score import dcg  # the frozen definition; that file is in the `judgeset` facet

OUT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "pg-notes"
RAW = OUT / "raw"
K = 10
ARMS = ["act", "body"]
BOOTSTRAP = 20000
SEED = 20261118
NULL_SEEDS = 50

# THERE IS NO MDE CONSTANT, and that is a fix. This script carried `MDE = 0.030`, copied from
# `rerank_score.py` where it was measured on a DIFFERENT statistic (reordering one arm's top-10,
# sd ~0.068). The paired differences here have sd 0.302, so the minimum detectable effect at
# n=40 is 2.8*sd/sqrt(n) = 0.134 -- four times larger. The constant silently suppressed this
# script's own "(below this set's MDE)" annotation on a result that was below it. An MDE is a
# property of the measurement, never a constant to import, so it is computed from the data.


def mde_of(diffs) -> float:
    """Minimum detectable effect, paired, alpha .05, power .80 -- same formula as
    judgeset_score.py's `2.8 * sd / sqrt(n)`, but over THESE differences."""
    return 2.8 * statistics.stdev(diffs) / math.sqrt(len(diffs))


def sign_test(diffs) -> float:
    """Exact two-sided sign test over the non-ties. Reported next to the mean because the mean
    and the median disagree here, and a mean carried by three queries is not a central
    tendency."""
    w = sum(1 for x in diffs if x > 1e-9)
    ls = sum(1 for x in diffs if x < -1e-9)
    n = w + ls
    if n == 0:
        return 1.0
    k = min(w, ls)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def ndcg_of(ids, grades: dict, ideal: float) -> float:
    return dcg([grades.get(i, 0) for i in ids[:K]]) / ideal if ideal else 0.0


def boot(diffs, tag: str) -> tuple[float, float]:
    """Percentile bootstrap with an INDEPENDENT generator seeded from `tag`.

    It used to take a shared `random.Random` and was called three times from main(), so each
    call drew from a different position of one mutated stream -- which is how the same 40
    differences printed `SIGNIFICANT` (lo = +0.000) for the headline and `GATE: NOT PASSED`
    (lo <= 0) two calls later. The interval quoted was a different draw from the one that
    decided the gate. Seeding per call makes the result a function of the data and the tag only.

    The lower bound here sits on zero whatever the seed: measured across 200 seeds it is
    negative in 23% of them. That is reported rather than hidden, because a CI whose sign is a
    coin flip on the RNG is not evidence of a direction.
    """
    rng = random.Random(f"{SEED}:{tag}")
    means = sorted(sum(rng.choices(diffs, k=len(diffs))) / len(diffs)
                   for _ in range(BOOTSTRAP))
    return means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP)]


def boot_sign_stability(diffs, tag: str, seeds: int = 200) -> float:
    """Fraction of RNG seeds for which the bootstrap lower bound is above zero."""
    out = 0
    for s in range(seeds):
        rng = random.Random(f"{tag}:{s}")
        means = sorted(sum(rng.choices(diffs, k=len(diffs))) / len(diffs)
                       for _ in range(1000))
        out += means[25] > 0
    return out / seeds


def headline_pool(recs, judged, swap: str | None = None) -> list[float]:
    """The primary endpoint recomputed from the raw records, body - act, per query.

    `swap` replaces the body arm's ranking with that arm's, which is what makes the IDENTITY
    control non-vacuous: it drives the real headline path with substituted DATA rather than
    asserting an algebraic identity over numbers already computed.
    """
    out = []
    for rec in recs:
        g = judged[rec["query"]]
        ideal = dcg(sorted(g.values(), reverse=True)[:K])
        a = [i for i, _ in rec["arms"]["act"]]
        b = [i for i, _ in rec["arms"][swap or "body"]]
        out.append(ndcg_of(b, g, ideal) - ndcg_of(a, g, ideal))
    return out


def main() -> None:
    raw = json.loads((RAW / "body-repr-ab-raw.json").read_text())
    judged = json.loads((OUT / "judgeset-judgments.json").read_text())
    pooled_raw = json.loads((OUT / "judgeset-raw.json").read_text())
    distractors = set(raw["distractors"])
    trunc = raw["truncated"]
    recs = raw["queries"]
    print(f"{len(recs)} queries, model {raw['model']}, max_tokens {raw['max_tokens']}, "
          f"{len(distractors)} distractors (the MDE is computed per endpoint below, "
          f"not carried as a constant)\n")

    # Which documents the production arm misses -- the recall-shaped subgroup. Recomputed here
    # from the frozen pooled run rather than passed in, so the two files cannot drift.
    missed, found = set(), set()
    for r in pooled_raw["queries"]:
        g = judged[r["query"]]
        top = set(r["arms"]["vec"][:K])
        best = {i for i, v in sorted(g.items(), key=lambda kv: -kv[1])[:K] if v >= 1}
        missed |= best - top
        found |= best & top

    per: dict[str, dict] = {}
    ungraded = []
    for rec in recs:
        q = rec["query"]
        g = judged[q]
        ideal = dcg(sorted(g.values(), reverse=True)[:K])
        per[q] = {}
        for arm in ARMS:
            pool_ids = [i for i, _ in rec["arms"][arm]]
            mini_ids = [i for i, _ in rec["arms_mini"][arm]]
            bad = [i for i in pool_ids[:K] if i not in g]
            if bad:
                ungraded.append((q, arm, bad))
            per[q][arm] = {
                "pool": ndcg_of(pool_ids, g, ideal),
                "mini": ndcg_of(mini_ids, g, ideal),
                "dis_in_top10": sum(1 for i in mini_ids[:K] if i in distractors and i not in g),
                "rank": {i: n for n, i in enumerate(pool_ids)},
            }
    if ungraded:
        for q, arm, bad in ungraded[:5]:
            print(f"UNGRADED {arm} on {q}: {bad}")
        raise SystemExit("refusing to score: a pool ranking contains an unjudged document")

    print(f"{'arm':<6} {'POOL':>7} {'MINI':>7} {'dis/10':>7}")
    for arm in ARMS:
        print(f"{arm:<6} {statistics.mean(per[q][arm]['pool'] for q in per):7.3f} "
              f"{statistics.mean(per[q][arm]['mini'] for q in per):7.3f} "
              f"{statistics.mean(per[q][arm]['dis_in_top10'] for q in per):7.2f}")

    print("\npaired per-query difference, body - act, 20,000 bootstrap resamples, 95% CI")
    print("  the MEAN is reported beside the MEDIAN and the sign test on purpose: they disagree,")
    print("  and three of the forty queries carry most of the mean.")
    headline = {}
    for key in ("pool", "mini"):
        d = [per[q]["body"][key] - per[q]["act"][key] for q in per]
        lo, hi = boot(d, key)
        w = sum(1 for x in d if x > 1e-9)
        ls = sum(1 for x in d if x < -1e-9)
        mde = mde_of(d)
        mark = "SIGNIFICANT" if lo > 0 or hi < 0 else "inside noise"
        if abs(statistics.mean(d)) < mde:
            mark = "BELOW MDE"
        headline[key] = statistics.mean(d)
        # MINI cannot be read as a gain or a loss -- see the withdrawal in the module docstring.
        # Printing it with the same verdict as the primary endpoint is how a number that looks
        # right survives being wrong, so it is labelled at the point of print.
        note = "  <- WITHDRAWN: penalises out-of-pool hits; descriptive only" if key == "mini" \
            else ""
        top3 = sum(sorted(d, reverse=True)[:3])
        print(f"  {key:<5} mean {statistics.mean(d):+.3f}  median {statistics.median(d):+.3f}  "
              f"[{lo:+.3f}, {hi:+.3f}]  {mark:<12}  W/L/T {w}/{ls}/{len(d) - w - ls}{note}")
        print(f"        MDE of THESE differences at n={len(d)}: {mde:.3f} "
              f"(sd {statistics.stdev(d):.3f})   sign test p={sign_test(d):.3f}   "
              f"top-3 wins are {top3 / sum(d):.0%} of the total" if sum(d) else "")
        if key == "pool":
            frac = boot_sign_stability(d, key)
            print(f"        bootstrap lower bound > 0 in {frac:.0%} of 200 RNG seeds "
                  f"-- a sign that depends on the seed is not a direction")

    # The recall-shaped cut, WITH THE BASELINE THAT MAKES IT READABLE.
    #
    # The subgroups are defined by the control arm's OWN top-10, so MISSED documents sit near
    # rank 17.5 of a 28-document pool and FOUND ones near 4.2. Any reordering at all moves the
    # first group up and the second down -- a floor and a ceiling, not a representation effect.
    # Measured: a uniformly random permutation of the same pools, carrying zero information,
    # moves MISSED up 4.12 ranks and FOUND down 8.95. So the body arm's -4.58 is what chance
    # does, and its +4.15 is BETTER than chance. The first version of this script printed the
    # two raw deltas and called them a trade; both halves were wrong, in opposite directions.
    #
    # The comparison that means something is therefore body MINUS the null arm, which is what
    # the `vs chance` column is.
    def deltas(rank_of, ids):
        rows = [(per[q]["act"]["rank"][i], rank_of(q, i))
                for q in per for i in ids
                if i in per[q]["act"]["rank"] and i in per[q]["body"]["rank"]]
        return (statistics.mean(x for x, _ in rows),
                statistics.mean(y for _, y in rows), len(rows)) if rows else (0.0, 0.0, 0)

    null_rank: list[dict] = []
    for s in range(NULL_SEEDS):
        r = random.Random(f"null:{SEED}:{s}")
        m = {}
        for rec in recs:
            ids = [i for i, _ in rec["arms"]["body"]]
            r.shuffle(ids)
            m[rec["query"]] = {i: n for n, i in enumerate(ids)}
        null_rank.append(m)

    print("\nmean rank inside the pool (0 = first; negative delta = ranked higher).")
    print("  `chance` is a uniformly random permutation of the same pool, 50 seeds: an arm with")
    print("  NO information. The subgroups come from the control arm's own top-10, so chance is")
    print("  not zero -- it is the floor/ceiling of the definition.")
    print(f"  {'subgroup':<26} {'n':>4} {'act':>6} {'body':>6} {'delta':>7} {'chance':>7} "
          f"{'vs chance':>10}")
    for label, ids in (("MISSED by production vec", missed), ("FOUND by production vec", found)):
        a_, b_, n = deltas(lambda q, i: per[q]["body"]["rank"][i], ids)
        nulls = [deltas(lambda q, i, m=m: m[q][i], ids)[1] - a_ for m in null_rank]
        ch = statistics.mean(nulls)
        print(f"  {label:<26} {n:4d} {a_:6.2f} {b_:6.2f} {b_ - a_:+7.2f} {ch:+7.2f} "
              f"{(b_ - a_) - ch:+10.2f}")
    print("  n is (query, document) PAIRS across 40 queries, not independent observations: one")
    print("  query contributes up to 10 of them, so a plain mean understates the spread.")

    # Split by truncation. The gap between the two subgroups is -2.53 ranks with a
    # cluster-bootstrap 95% CI of [-5.25, +1.17], and ~40% of it is reproduced by the null
    # permutation -- so the supportable statement is "no evidence that truncation limits the
    # effect", NOT "the truncated subgroup moves more", which is what the first version said.
    # The truncated pairs live in only 15 of the 40 queries.
    print("\nsame cut, split by whether the body was TRUNCATED at 512 tokens")
    print("  (chance = the same null permutation; the whole/truncated gap is NOT "
          "distinguishable from zero)")
    for label, ids in (("MISSED", missed), ("FOUND", found)):
        for tlabel, want in (("whole", False), ("truncated", True)):
            rows = [(per[q][ARMS[0]]["rank"][i], per[q][ARMS[1]]["rank"][i])
                    for q in per for i in ids
                    if i in per[q]["act"]["rank"] and trunc.get(i, False) is want]
            if not rows:
                continue
            a_ = statistics.mean(x for x, _ in rows)
            b_ = statistics.mean(y for _, y in rows)
            sel = [(q, i) for q in per for i in ids
                   if i in per[q]["act"]["rank"] and trunc.get(i, False) is want]
            ch = statistics.mean(
                statistics.mean(m[q][i] for q, i in sel) - a_ for m in null_rank)
            print(f"  {label:<7} {tlabel:<10} n={len(rows):4d} in "
                  f"{len({q for q, _ in sel}):2d} queries  chance {ch:+6.2f}  "
                  f"act {a_:5.2f}  body {b_:5.2f}  "
                  f"delta {b_ - a_:+.2f}")

    # ---------- controls that can fail ----------
    print("\n=== controls that can FAIL ===")
    bad = 0

    # IDENTITY: drive the real headline path with the body arm's ranking REPLACED by the act
    # arm's. Must be exactly 0.000000. Not an algebraic identity over already-computed numbers
    # -- the substitution happens in the data, so a headline that read the wrong field, or
    # mixed the pool and mini rankings, would show up here as non-zero.
    sub = statistics.mean(headline_pool(recs, judged, swap="act"))
    real = statistics.mean(headline_pool(recs, judged))
    ok = abs(sub) < 1e-12 and abs(real - headline["pool"]) < 1e-9
    bad += not ok
    print(f"  IDENTITY  body:=act gives {sub:+.12f}, and the recomputed real headline "
          f"{real:+.6f} matches {headline['pool']:+.6f}  {'ok' if ok else 'FAIL'}")

    # MUTATION: rotate the body arm's pool ranking by one position. The headline MUST move --
    # if it does not, the scorer is not reading the ranking it claims to.
    mut = []
    for rec in recs:
        q = rec["query"]
        g = judged[q]
        ideal = dcg(sorted(g.values(), reverse=True)[:K])
        ids = [i for i, _ in rec["arms"]["body"]]
        mut.append(ndcg_of(ids[1:] + ids[:1], g, ideal) - per[q]["act"]["pool"])
    # The threshold is the endpoint's own MDE, not 1e-6. At 1e-6 this control passed on an
    # inverted dataset having moved by 0.001, which is a detector of nothing: it has to show
    # the scorer reads the order WELL ENOUGH TO MATTER, not merely that a float changed.
    dmut = abs(statistics.mean(mut) - headline["pool"])
    thresh = mde_of([per[q]["body"]["pool"] - per[q]["act"]["pool"] for q in per]) / 4
    moved = dmut > thresh
    bad += not moved
    print(f"  MUTATION  rotated body = {statistics.mean(mut):+.3f} against the real "
          f"{headline['pool']:+.3f}, moved {dmut:.3f} > {thresh:.3f}  "
          f"{'ok' if moved else 'FAIL (the scorer barely reads the order)'}")

    # RANDOM: a seeded shuffle of the same pool. Must be clearly worse than BOTH arms -- this is
    # the control whose absence made the cross-encoder write-up wrong about which direction the
    # existing order mattered in.
    r2 = random.Random(SEED)
    rnd = []
    for rec in recs:
        q = rec["query"]
        g = judged[q]
        ideal = dcg(sorted(g.values(), reverse=True)[:K])
        ids = [i for i, _ in rec["arms"]["body"]]
        r2.shuffle(ids)
        rnd.append(ndcg_of(ids, g, ideal))
    rm = statistics.mean(rnd)
    worse = rm < min(statistics.mean(per[q][a]["pool"] for q in per) for a in ARMS) - 0.02
    bad += not worse
    print(f"  RANDOM    shuffled pool nDCG@10 = {rm:.3f} against act "
          f"{statistics.mean(per[q]['act']['pool'] for q in per):.3f} / body "
          f"{statistics.mean(per[q]['body']['pool'] for q in per):.3f}  "
          f"{'ok' if worse else 'FAIL (ordering does not matter here at all)'}")

    # The MINI endpoint must be no better than POOL for either arm: adding 2,000 documents
    # cannot help. A violation means the two endpoints do not share their ideal.
    for arm in ARMS:
        p = statistics.mean(per[q][arm]["pool"] for q in per)
        m = statistics.mean(per[q][arm]["mini"] for q in per)
        ok = m <= p + 1e-9
        bad += not ok
        print(f"  MONOTONE  {arm}: mini {m:.3f} <= pool {p:.3f}  {'ok' if ok else 'FAIL'}")

    print(f"\n{'ALL CONTROLS PASS' if bad == 0 else str(bad) + ' CONTROL FAILURES'}")

    # THE SAME DRAW the headline printed, by tag -- not a fresh one. This line and the headline
    # used to disagree on the sign of the lower bound because `boot` shared one mutating RNG
    # across three calls, so "SIGNIFICANT" and "NOT PASSED" were printed from different draws
    # of the same data.
    d = [per[q]["body"]["pool"] - per[q]["act"]["pool"] for q in per]
    lo, _ = boot(d, "pool")
    gate = "PASS" if headline["pool"] > 0 and lo > 0 and abs(headline["pool"]) > mde_of(d) \
        else "NOT PASSED"
    print(f"\nGATE (positive with a 95% CI excluding 0 on the POOL endpoint): {gate}")
    # "NOT PASSED" is not "STOP", and the difference matters. The POOL endpoint ranks only the
    # pool, and the pool is the depth-10 union of the four OLD arms -- so a document this arm
    # surfaces that none of them did cannot appear in it at all. The endpoint therefore
    # UNDER-credits the arm by construction, in the one direction the arm is supposed to help,
    # and the distractor inspection above shows that is not hypothetical. A positive but
    # CI-touching-zero result here is inconclusive, not negative.
    # The honest reading, and the first version of this text was not it. It said "INCONCLUSIVE,
    # not negative" and explained the null by the pool bias -- which makes the null
    # unfalsifiable: any absence of effect can be attributed to an under-crediting endpoint
    # forever. Two separate statements instead, one measured and one not:
    print("  MEASURED: no detectable effect on this endpoint. Mean below the MDE, median 0.000,")
    print("            sign test p=1.00, and 76% of the mean from 3 of 40 queries.")
    print("  UNTESTED: this endpoint ranks only the pool, built from the four OLD arms, so a")
    print("            correct document only this arm finds cannot appear in it. That is a")
    print("            reason the endpoint under-credits, NOT evidence that it did -- settling")
    print("            it means judging the body arm's out-of-pool hits.")
    print(f"  {math.ceil((2.8 * statistics.stdev([per[q]['body']['pool'] - per[q]['act']['pool'] for q in per]) / 0.05) ** 2)} "
          f"queries would be needed to resolve a 0.05 difference at this variance")


if __name__ == "__main__":
    main()
