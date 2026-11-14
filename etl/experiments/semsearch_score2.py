import json, os, statistics as st
D = os.path.dirname(os.path.abspath(__file__))
# Extended pool: original judgments + the 234 newly judged documents.
rel = {}
for sh, ju in (("judge_sheet.json","judgments.json"), ("judge_sheet2.json","judgments2.json")):
    sheet = json.load(open(f"{D}/{sh}")); judg = json.load(open(f"{D}/{ju}"))
    for s in sheet:
        byn = {c["n"]: c["id"] for c in s["candidates"]}
        rel.setdefault(s["query"], set()).update(byn[n] for n in judg[s["query"]])

fac = json.load(open(f"{D}/factorial_raw.json"))
ARMS = ["A_novocab_translate","C_vocab_translate","D_novocab_expand","B_vocab_expand"]
LBL  = {"A_novocab_translate":"A 无词表+直译","C_vocab_translate":"C 有词表+直译",
        "D_novocab_expand":"D 无词表+展开","B_vocab_expand":"B 有词表+展开"}

print(f"{'query':30} " + " ".join(f"{LBL[a][:2]:>6}" for a in ARMS) + f" {'|rel|':>6}")
print("-"*72)
tot = {a: 0 for a in ARMS}; per = {a: [] for a in ARMS}
for q, res in fac.items():
    R = rel[q]; row = []
    for a in ARMS:
        k = sum(1 for h in res[a]["hits"] if h["id"] in R)
        tot[a] += k; per[a].append(k); row.append(k)
    print(f"{q[:28]:30} " + " ".join(f"{v:>5}/10" for v in row) + f" {len(R):>6}")
print("-"*72)
for a in ARMS:
    print(f"{LBL[a]:14} precision@10 = {tot[a]}/150 = {tot[a]/150*100:5.1f}%   "
          f"(per-query sd {st.stdev(per[a]):.1f})")

print("\n=== 分离两个因子 ===")
A,C,Dd,B = (tot[x]/150*100 for x in ARMS)
print(f"词表的作用(固定=直译):   C - A = {C:5.1f} - {A:5.1f} = {C-A:+5.1f} 点")
print(f"词表的作用(固定=展开):   B - D = {B:5.1f} - {Dd:5.1f} = {B-Dd:+5.1f} 点")
print(f"展开的作用(固定=无词表): D - A = {Dd:5.1f} - {A:5.1f} = {Dd-A:+5.1f} 点")
print(f"展开的作用(固定=有词表): B - C = {B:5.1f} - {C:5.1f} = {B-C:+5.1f} 点")
print(f"交互项:                 (B-C)-(D-A) = {(B-C)-(Dd-A):+5.1f} 点")
print(f"\n原始 A->B 的总差距 = {B-A:+.1f} 点  (当初归因不明的那 24 点)")
