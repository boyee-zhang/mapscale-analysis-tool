"""Aggregate the fan-out. Only the caller has the stratum weights, so only the caller may
compute a rate — each worker saw one stratum and was forbidden from computing anything.

Also validates the workers' output rather than trusting it, and prints a spot-check sample
so the labels can be audited without re-reading the source.
"""
import json
import math
import pathlib
import sys
import random

SP = pathlib.Path(sys.argv[1])
LABELS = ["boilerplate_procedural", "navigational_duplicate", "substantive_unique", "unclear"]
# Corpus sizes per doc_type (full table, not the sample).
N_CORPUS = {
    "evenementenvergunning": 34481, "andere vergunning": 32587,
    "omgevingsvergunning": 17813, "verkeersbesluit of -mededeling": 3425,
    "exploitatievergunning": 2748, "omgevingsmelding": 1331,
}
PER_STRATUM = 50
TOTAL_ROWS = 94183

def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    m = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return (max(0.0, c - m), min(1.0, c + m))

idx = json.loads((SP / "judge-index.json").read_text())
census = json.loads((SP / "census.json").read_text())

rows, problems = {}, []
for dt, v in idx.items():
    lf = SP / v["file"].replace("judge-", "labels-")
    if not lf.exists():
        problems.append(f"{dt}: MISSING {lf.name}")
        continue
    d = json.loads(lf.read_text())
    units = d.get("units", [])
    shard = json.loads((SP / v["file"]).read_text())
    want = shard["n_headings"]
    if len(units) != want:
        problems.append(f"{dt}: {len(units)} units labelled, shard has {want} headings")
    bad = sorted({u.get("label") for u in units} - set(LABELS))
    if bad:
        problems.append(f"{dt}: invented labels {bad}")
    ids = [u.get("unit_id") for u in units]
    if len(set(ids)) != len(ids):
        problems.append(f"{dt}: {len(ids)-len(set(ids))} duplicate unit_id")
    miss = [u for u in units if not u.get("evidence") or not u.get("reason")]
    if miss:
        problems.append(f"{dt}: {len(miss)} units without evidence or reason")
    rows[dt] = units

if problems:
    print("VALIDATION PROBLEMS:")
    for prob in problems:
        print("  -", prob)
    print()

print(f"{'doc_type':32} {'N_corpus':>9} {'hdrs':>5} " + " ".join(f"{lab[:12]:>13}" for lab in LABELS))
print("-" * 112)
est_docs_hurt = est_sub_headings = 0.0
for dt, units in rows.items():
    c = {lab: sum(1 for u in units if u.get("label") == lab) for lab in LABELS}
    n = N_CORPUS[dt]
    # docs in this stratum's sample that lose at least one unique fact
    hurt_ids = {u["unit_id"].split("#")[0] for u in units
                if u.get("label") == "substantive_unique"}
    frac_docs_hurt = len(hurt_ids) / PER_STRATUM
    est_docs_hurt += n * frac_docs_hurt
    est_sub_headings += n * (c["substantive_unique"] / PER_STRATUM)
    print(f"{dt:32} {n:>9,} {len(units):>5} " + " ".join(f"{c[lab]:>13}" for lab in LABELS))

print()
cov = sum(N_CORPUS.values())
print(f"strata cover {cov:,} of {TOTAL_ROWS:,} rows ({100*cov/TOTAL_ROWS:.1f}%)")
print(f"ESTIMATED documents in the corpus losing >=1 unique fact: {est_docs_hurt:,.0f} "
      f"({100*est_docs_hurt/cov:.2f}% of covered rows)")
print(f"ESTIMATED substantive_unique headings corpus-wide:        {est_sub_headings:,.0f}")

# per-stratum Wilson CI on "a sampled document loses >=1 unique fact"
print(f"\n{'doc_type':32} {'docs_hurt/50':>13} {'95% CI':>22}")
for dt, units in rows.items():
    k = len({u["unit_id"].split("#")[0] for u in units
             if u.get("label") == "substantive_unique"})
    lo, hi = wilson(k, PER_STRATUM)
    print(f"{dt:32} {k:>7}/{PER_STRATUM:<5} {100*lo:>9.1f}% – {100*hi:<9.1f}%")

# character loss, measured not judged
print(f"\n{'doc_type':32} {'incidence':>10} {'pooled_chars_lost':>18}")
for dt in rows:
    c = census[dt]
    print(f"{dt:32} {str(c['incidence_pct'])+'%':>10} {str(c['pooled_pct_chars_lost'])+'%':>18}")
# Weighting per-stratum PERCENTAGES by document count is wrong and was published as 0.456%
# before it was caught: the heading-rich strata have ~3,000-character bodies against 670 for
# omgevingsmelding, so a ratio of ratios is not the corpus ratio. Weight CHARACTERS instead.
# Independent check: plain random n=400 twice gave 0.602% / 0.654% (bootstrap 95% CI
# 0.579-0.727%), which the character weighting lands inside and the document weighting does not.
num = den = 0.0
for dt in rows:
    sh = json.loads((SP / census[dt]["shard_file"]).read_text())
    ok = [r for r in sh["rows"] if r["status"] == "ok"]
    if not ok:
        continue
    scale = N_CORPUS[dt] / len(ok)          # this stratum's sampling weight
    num += scale * sum(r["chars_tussenkop"] for r in ok)
    den += scale * sum(r["chars_tussenkop"] + r["chars_al"] for r in ok)
bad = sum(N_CORPUS[dt] * census[dt]["pooled_pct_chars_lost"] for dt in rows) / cov
print(f"corpus char-weighted loss : {100*num/den:.3f}%   <- use this")
print(f"(document-weighted ratio-of-percentages, WRONG, kept to show the artifact: {bad:.3f}%)")

print("\n=== SPOT CHECK (audit these before believing the table) ===")
random.seed(7)
for lab in LABELS:
    pool = [(dt, u) for dt, us in rows.items() for u in us if u.get("label") == lab]
    print(f"\n--- {lab}  (n={len(pool)})")
    for dt, u in random.sample(pool, min(3, len(pool))):
        print(f"  [{dt}] {u['unit_id']}")
        print(f"    reason  : {u.get('reason','')[:150]}")
        ev = str(u.get("evidence", "")).replace("\n", " ")
        print(f"    evidence: {ev[:220]}")

json.dump({"est_docs_hurt": est_docs_hurt, "est_sub_headings": est_sub_headings,
           "coverage": cov, "problems": problems,
           "per_stratum": {dt: {lab: sum(1 for u in us if u.get("label") == lab) for lab in LABELS}
                           for dt, us in rows.items()}},
          open(SP / "aggregate.json", "w"), indent=1, ensure_ascii=False)
print(f"\nwrote {SP / 'aggregate.json'}")
