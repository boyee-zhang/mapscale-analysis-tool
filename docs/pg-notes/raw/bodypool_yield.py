import json, statistics, math, sys
OUT="docs/pg-notes/"
# which label set to read; v1 judged from a truncated summary, v2 re-graded from the full one
LABELS = sys.argv[1] if len(sys.argv) > 1 else OUT+"bodypool-judgments.json"
print("labels:", LABELS)
old=json.load(open(OUT+"judgeset-judgments.json"))
fresh=json.load(open(LABELS))
armf=json.load(open(OUT+"bodypool-arm.json"))
pool={q:dict(v) for q,v in old.items()}
for q,v in fresh.items():
    for d,g in v.items():
        pool.setdefault(q,{}).setdefault(d,int(g))
body_new=[tuple(x) for x in armf["new_pairs"]]
ctl_new=[tuple(x) for x in armf["control_pairs"]]
def rate(pairs,label):
    rel=sum(1 for q,d in pairs if pool[q][d]>=1); g2=sum(1 for q,d in pairs if pool[q][d]==2)
    print(f"{label:34s} n={len(pairs):4d}  relevant {rel:4d} ({rel/len(pairs):.1%})  grade2 {g2:4d} ({g2/len(pairs):.1%})")
print("OUT-OF-POOL YIELD -- the control is the second line")
rate(body_new,"body arm top-10 (new)"); rate(ctl_new,"production vec ranks 11-15 (new)")
pq=[]
for q in old:
    b=[d for qq,d in body_new if qq==q]; c=[d for qq,d in ctl_new if qq==q]
    if b and c:
        pq.append(sum(1 for d in b if pool[q][d]>=1)/len(b) - sum(1 for d in c if pool[q][d]>=1)/len(c))
sd=statistics.stdev(pq)
print(f"\npaired per-query yield difference (body - control): mean {statistics.mean(pq):+.3f}  "
      f"median {statistics.median(pq):+.3f}  n={len(pq)}  MDE {2.8*sd/math.sqrt(len(pq)):.3f} (sd {sd:.3f})")
w=sum(1 for x in pq if x>0); l=sum(1 for x in pq if x<0)
print(f"W/L/T {w}/{l}/{len(pq)-w-l}")
