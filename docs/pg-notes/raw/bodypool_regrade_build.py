"""Blind re-grade sheet for every item whose summary was cut in the first judging pass.

The first pass judged from at most 150 characters (the sheet carries 300; the viewer cut again
to 150), and the cut fell on 52.6% of body-arm items against 1.0% of control items -- so the
success predicate was "this 150-character prefix says so", which is the CONTROL arm's own
retrieval input. This rebuilds those items with the FULL activity_text.

It does NOT show the body. The rubric judges the summary, and showing the body would change the
task rather than fix the asymmetry -- and would invalidate the calibration items, which were
judged summary-only on 2026-11-13.

Blind: shuffled with its own seed, no arm, no rank, and NOT the previous grade.
"""
import json, pathlib, random, asyncio, asyncpg
from etl.core import db
OUT = pathlib.Path("/Users/zby/dev/mapscale-analysis-tool/docs/pg-notes")
SP = pathlib.Path("/private/tmp/claude-501/-Users-zby-dev-mapscale-analysis-tool/4edc0ef8-40be-4b21-9cc4-52bad2b4f7a3/scratchpad")
SEED = 20261121

aff = [tuple(x) for x in json.loads((SP / "affected.json").read_text())]
sheet = json.loads((OUT / "bodypool-judge_sheet.json").read_text())
disp = {c["id"]: c for s in sheet for c in s["candidates"]}
ids = sorted({d for _, d in aff})

Q = """SELECT b.identifier, coalesce(t.activity_text,'') a FROM bekendmaking b
LEFT JOIN bekendmaking_search_text t USING (identifier) WHERE b.identifier = ANY($1::text[])"""

async def main():
    c = await asyncpg.connect(db.dsn())
    full = {r["identifier"]: r["a"] for r in await c.fetch(Q, ids)}
    await c.close()
    rng = random.Random(SEED)
    byq = {}
    for q, d in aff:
        byq.setdefault(q, []).append(d)
    out = []
    for q in sorted(byq):
        items = sorted(byq[q])
        rng.shuffle(items)
        out.append({"query": q, "candidates": [
            {"n": i + 1, "id": d, "title": disp[d]["title"], "doc_type": disp[d]["doc_type"],
             "gemeente": disp[d]["gemeente"], "text": full[d]}
            for i, d in enumerate(items)]})
    (SP / "regrade_sheet.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    n = sum(len(s["candidates"]) for s in out)
    print(f"{n} items over {len(out)} queries; full summary length "
          f"median {sorted(len(full[d]) for _, d in aff)[len(aff)//2]}, max {max(len(v) for v in full.values())}")
asyncio.run(main())
