"""Score the BODY arm against the ENLARGED pool, once the new pairs are judged.

    .venv/bin/python -m etl.experiments.body_pool_score \
        --judgments docs/pg-notes/bodypool-judgments.json

WHAT CHANGES WHEN THE POOL GROWS, stated before any number is printed. nDCG@10's denominator
is the ideal ranking of everything KNOWN relevant for that query. Judging the body arm's
candidates adds known-relevant documents, so the denominator rises for EVERY arm -- the
production arm's nDCG@10 will come out LOWER than the 0.669 recorded in
`docs/pg-notes/semsearch-body-repr.md`, with its top-10 unchanged. That is not a regression and
the two numbers are not comparable: the earlier one was measured against a pool the production
arm helped build and this arm did not. Only the PAIRED difference inside one run is meaningful,
which is why that is the headline.

THE RECALL NUMBER IS THE POINT. The gate measured ordering inside a fixed set and could not see
recall by construction. Here both arms' top-10s are fully judged, so the question the 38x
out-of-pool rate raised -- are those documents relevant or noise -- is answered directly by
counting them.

CALIBRATION IS A CONTROL THAT CAN FAIL. 10 items on the sheet were already judged on
2026-11-13. If this session's grades disagree with those, the two judging sessions are not one
instrument and the enlarged pool is not comparable to the old one. The script REFUSES rather
than reporting, because a drifted judge produces numbers that look exactly like real ones.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import statistics

from .judgeset_score import dcg  # frozen; that file is in the `judgeset` facet and is not edited

OUT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "pg-notes"
BOOTSTRAP = 20000
SEED = 20261119
POOL_DEPTH = 10


def ndcg(ranked_grades, all_grades, k=POOL_DEPTH) -> float:
    ideal = dcg(sorted(all_grades, reverse=True)[:k])
    return dcg(ranked_grades[:k]) / ideal if ideal else 0.0


def mde_of(diffs) -> float:
    """A property of THESE differences. The gate shipped a constant copied from another
    experiment and it silently suppressed the script's own below-resolution annotation."""
    return 2.8 * statistics.stdev(diffs) / math.sqrt(len(diffs))


def boot(diffs, tag):
    """An independent generator per call. A shared mutating RNG made the gate's headline
    interval a different draw from the one that decided it, and the two contradicted."""
    rng = random.Random(f"{SEED}:{tag}")
    n = len(diffs)
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(BOOTSTRAP))
    return means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP)]


def sign_test(diffs) -> float:
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    n = pos + neg
    if not n:
        return 1.0
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--judgments", required=True,
                    help="this session's grades: {query: {id: 0|1|2}} or {'query\\tid': grade}")
    ap.add_argument("--allow-drift", action="store_true",
                    help="report anyway after a calibration failure (say so in the write-up)")
    args = ap.parse_args()

    old = json.loads((OUT / "judgeset-judgments.json").read_text())
    armf = json.loads((OUT / "bodypool-arm.json").read_text())
    key = json.loads((OUT / "bodypool-calibration_key.json").read_text())
    raw = json.loads((OUT / "judgeset-raw.json").read_text())
    fresh_in = json.loads(pathlib.Path(args.judgments).read_text())

    fresh: dict[str, dict[str, int]] = {}
    for k, v in fresh_in.items():
        if isinstance(v, dict):
            fresh.setdefault(k, {}).update({d: int(g) for d, g in v.items()})
        else:
            q, d = k.split("\t")
            fresh.setdefault(q, {})[d] = int(v)
    print(f"{sum(len(v) for v in fresh.values())} grades in this session's file")

    # ---- CALIBRATION, before anything is scored.
    drift = []
    for k, was in key.items():
        q, d = k.split("\t")
        now = fresh.get(q, {}).get(d)
        if now is None:
            drift.append((q, d, was, None))
        elif int(now) != int(was):
            drift.append((q, d, was, int(now)))
    agree = len(key) - len([x for x in drift if x[3] is not None])
    unjudged = [x for x in drift if x[3] is None]
    print(f"CALIBRATION {agree}/{len(key)} exact agreement with 2026-11-13"
          f"{f', {len(unjudged)} not graded' if unjudged else ''}")
    for q, d, was, now in drift:
        print(f"   {d}  was {was}  now {now}  |  {q}")
    hard = [x for x in drift if x[3] is not None and abs(x[2] - x[3]) >= 2]
    if unjudged or agree < 7 or hard:
        msg = ("CALIBRATION FAILED: the two judging sessions are not one instrument, so the "
               "enlarged pool is not comparable to the recorded one")
        if not args.allow_drift:
            raise SystemExit(msg)
        print(f"!! {msg} -- reporting anyway because --allow-drift")

    # ---- The enlarged pool. Existing grades win: this session may not overwrite them.
    pool: dict[str, dict[str, int]] = {q: dict(v) for q, v in old.items()}
    added = 0
    for q, v in fresh.items():
        for d, g in v.items():
            if d not in pool.setdefault(q, {}):
                pool[q][d] = int(g)
                added += 1
    print(f"pool: {sum(len(v) for v in old.values())} -> {sum(len(v) for v in pool.values())} "
          f"({added} added)")

    prod = {r["query"]: r["arms"]["vec"][:POOL_DEPTH] for r in raw["queries"]}
    body = {q: ids[:POOL_DEPTH] for q, ids in armf["arm"].items()}
    queries = [q for q in old if q in body and q in prod]

    # An arm is only scorable where every document it ranks is graded. Anything else is the
    # pool-bias failure this whole exercise exists to remove.
    for q in queries:
        for name, ids in (("vec", prod[q]), ("body", body[q])):
            ungraded = [d for d in ids if d not in pool[q]]
            if ungraded:
                raise SystemExit(f"{name} top-{POOL_DEPTH} for {q!r} has {len(ungraded)} "
                                 f"ungraded documents: {ungraded[:3]}")

    rows, diffs = [], []
    for q in queries:
        allg = list(pool[q].values())
        a = ndcg([pool[q][d] for d in prod[q]], allg)
        b = ndcg([pool[q][d] for d in body[q]], allg)
        rows.append((q, a, b))
        diffs.append(b - a)

    mean_a = statistics.mean(r[1] for r in rows)
    mean_b = statistics.mean(r[2] for r in rows)
    head = statistics.mean(diffs)
    lo, hi = boot(diffs, "pool")
    m = mde_of(diffs)
    w = sum(1 for d in diffs if d > 1e-12)
    ls = sum(1 for d in diffs if d < -1e-12)
    print(f"\narm        nDCG@10  (pool-complete, {len(queries)} queries)")
    print(f"vec        {mean_a:.3f}")
    print(f"body       {mean_b:.3f}")
    print(f"\npaired  mean {head:+.3f}  median {statistics.median(diffs):+.3f}  "
          f"[{lo:+.3f}, {hi:+.3f}]  {'BELOW MDE' if abs(head) < m else 'above MDE'}  "
          f"W/L/T {w}/{ls}/{len(diffs) - w - ls}")
    print(f"        MDE of THESE differences at n={len(diffs)}: {m:.3f} "
          f"(sd {statistics.stdev(diffs):.3f})   sign test p={sign_test(diffs):.3f}")

    # ---- THE RECALL NUMBER. Relevant documents one arm reaches and the other does not.
    only_b = only_a = 0
    for q in queries:
        sb, sa = set(body[q]), set(prod[q])
        only_b += sum(1 for d in sb - sa if pool[q][d] >= 1)
        only_a += sum(1 for d in sa - sb if pool[q][d] >= 1)
    print(f"\nRECALL at depth {POOL_DEPTH}, relevant (grade>=1) documents reached by one arm only")
    print(f"  body only: {only_b}   ({only_b / len(queries):.2f} per query)")
    print(f"  vec only:  {only_a}   ({only_a / len(queries):.2f} per query)")
    print(f"  net:       {only_b - only_a:+d}")

    new_rel = sum(1 for q in queries for d in body[q]
                  if d not in old.get(q, {}) and pool[q][d] >= 1)
    new_tot = sum(1 for q in queries for d in body[q] if d not in old.get(q, {}))
    print(f"  of the {new_tot} out-of-pool documents this arm surfaced, {new_rel} are relevant "
          f"({new_rel / new_tot:.1%})" if new_tot else "  no out-of-pool documents")

    print("\nper query")
    for q, a, b in sorted(rows, key=lambda r: r[2] - r[1]):
        print(f"  {b - a:+.3f}  vec {a:.3f}  body {b:.3f}  {q}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
