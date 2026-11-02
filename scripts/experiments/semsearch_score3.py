import json, os, random, statistics as st
D = os.path.dirname(os.path.abspath(__file__))
rel = {}
for sh, ju in (("judge_sheet.json","judgments.json"), ("judge_sheet2.json","judgments2.json")):
    sheet = json.load(open(f"{D}/{sh}")); judg = json.load(open(f"{D}/{ju}"))
    for s in sheet:
        byn = {c["n"]: c["id"] for c in s["candidates"]}
        rel.setdefault(s["query"], set()).update(byn[n] for n in judg[s["query"]])

fac = json.load(open(f"{D}/factorial_raw.json"))
raw = {r["query"]: r for r in json.load(open(f"{D}/raw.json"))}

arms = {}
for q, res in fac.items():
    for a in res:
        arms.setdefault(a, {})[q] = sum(1 for h in res[a]["hits"] if h["id"] in rel[q])
# original B, WITH its doc_type/activiteit filters, scored on the SAME extended pool
arms["B_vocab_expand_FILTERED"] = {
    q: sum(1 for h in raw[q]["arms"]["B_expanded"]["hits"] if h["id"] in rel[q]) for q in fac}
arms["A_orig_check"] = {
    q: sum(1 for h in raw[q]["arms"]["A_literal"]["hits"] if h["id"] in rel[q]) for q in fac}

for a in sorted(arms, key=lambda x: -sum(arms[x].values())):
    s = sum(arms[a].values())
    print(f"{a:28} {s:>3}/150 = {s/150*100:5.1f}%")

def paired(x, y, n=20000, seed=1):
    """Paired bootstrap on the 15 per-query differences."""
    d = [arms[x][q] - arms[y][q] for q in fac]
    rng = random.Random(seed)
    means = sorted(st.mean(rng.choices(d, k=len(d))) for _ in range(n))
    lo, hi = means[int(.025*n)], means[int(.975*n)]
    return st.mean(d), lo, hi, st.stdev(d)

print("\n=== 配对 bootstrap,每 query 的差值 (单位: 命中数/10) ===")
print(f"{'比较':52} {'均值':>6} {'95% CI':>18} {'结论'}")
print("-"*96)
for name, x, y in [
    ("词表的作用,固定直译      C - A", "C_vocab_translate","A_novocab_translate"),
    ("词表的作用,固定展开      B - D", "B_vocab_expand","D_novocab_expand"),
    ("展开的作用,固定无词表    D - A", "D_novocab_expand","A_novocab_translate"),
    ("展开的作用,固定有词表    B - C", "B_vocab_expand","C_vocab_translate"),
    ("硬过滤的作用            B_filt - B", "B_vocab_expand_FILTERED","B_vocab_expand"),
]:
    m, lo, hi, sd = paired(x, y)
    verdict = "显著" if (lo > 0 or hi < 0) else "噪声内,分不出"
    print(f"{name:52} {m:>+6.2f} [{lo:>+6.2f},{hi:>+6.2f}]  {verdict}")

print("\n=== 我当初报的那个结论,补算 ===")
for name, x, y in [("原始 A vs B(带过滤器) B_filt - A", "B_vocab_expand_FILTERED","A_novocab_translate"),
                   ("最好 vs 最差          C - A", "C_vocab_translate","A_novocab_translate")]:
    m, lo, hi, sd = paired(x, y)
    print(f"{name:40} {m:>+6.2f} [{lo:>+6.2f},{hi:>+6.2f}]  sd(差值)={sd:.2f}  "
          f"{'显著' if (lo>0 or hi<0) else '噪声内'}")

print("\n=== 要多少个 query 才能分出来?(配对 t, alpha=.05, power=.80) ===")
print(f"{'要检测的效应 (命中数/query)':34} {'≈ 百分点':>10} {'需要 n'}")
print("-"*62)
d_ref = [arms["C_vocab_translate"][q] - arms["A_novocab_translate"][q] for q in fac]
sd = st.stdev(d_ref)
print(f"(用 C-A 的差值 sd = {sd:.2f} 作为方差估计)")
for eff in (2.5, 1.7, 1.0, 0.5, 0.3):
    n = ((1.96 + 0.84) ** 2) * sd**2 / eff**2
    print(f"{eff:>26.1f}   {eff*10:>9.0f}   {n:>6.0f}")
