import json, os
D = os.path.dirname(os.path.abspath(__file__))
raw   = json.load(open(f"{D}/raw.json"))
sheet = json.load(open(f"{D}/judge_sheet.json"))
judg  = json.load(open(f"{D}/judgments.json"))

# n -> identifier, per query (the sheet is the only place provenance was stripped)
rel = {}
for s in sheet:
    byn = {c["n"]: c["id"] for c in s["candidates"]}
    rel[s["query"]] = {byn[n] for n in judg[s["query"]]}

print(f"{'query':36} {'A rel':>6} {'B rel':>6} {'pool':>5} {'winner':>8}")
print("-" * 68)
agg = {"A_literal": [0, 0], "B_expanded": [0, 0]}
wins = {"A": 0, "B": 0, "tie": 0}
for r in raw:
    q = r["query"]; R = rel[q]
    n = {}
    for arm in ("A_literal", "B_expanded"):
        hits = [h["id"] for h in r["arms"][arm]["hits"]]
        k = sum(1 for h in hits if h in R)
        n[arm] = k
        agg[arm][0] += k
        agg[arm][1] += len(hits)
    w = "A" if n["A_literal"] > n["B_expanded"] else ("B" if n["B_expanded"] > n["A_literal"] else "tie")
    wins[w] += 1
    print(f"{q:36} {n['A_literal']:>4}/10 {n['B_expanded']:>4}/10 {len(R):>5} {w:>8}")

print("-" * 68)
for arm in ("A_literal", "B_expanded"):
    k, tot = agg[arm]
    print(f"{arm:12}  precision@10 = {k}/{tot} = {k/tot*100:.1f}%")
print(f"\nper-query wins: B={wins['B']}  A={wins['A']}  tie={wins['tie']}")
tot_rel = sum(len(v) for v in rel.values())
print(f"pooled relevant total: {tot_rel} / 285 candidates")
for arm in ("A_literal", "B_expanded"):
    k = agg[arm][0]
    print(f"{arm:12}  recall over pool = {k}/{tot_rel} = {k/tot_rel*100:.1f}%")

print("\n=== terms each arm produced ===")
for r in raw:
    print(f"\n{r['query']}")
    for arm in ("A_literal", "B_expanded"):
        a = r["arms"][arm]
        extra = ""
        if a["doc_type"] or a["activiteit"]:
            extra = f"   filters: doc_type={a['doc_type']} activiteit={a['activiteit']}"
        print(f"  {arm:11} {a['terms']}{extra}")
