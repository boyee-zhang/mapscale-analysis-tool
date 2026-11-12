"""For every sampled doc that HAS a tussenkop, re-fetch and store the body as an ORDERED
element sequence so a heading can be compared with the paragraphs around it.

Uses ElementTree, not a regex: a single-pass regex with an alternation and a backreference
(`<(tussenkop|al)\\b[^>]*>(.*?)</\\1>`) swallows a heading nested inside an <al>, and reported
9 headings where two independent scans found 41. A parser's failure mode is an exception;
a regex's is a plausible wrong answer. sample.py's per-tag count is kept as the control and
any disagreement is fatal, not a warning.
"""
import asyncio
import json
import re
import sys
import pathlib
import xml.etree.ElementTree as ET

SP = pathlib.Path(sys.argv[1])
DOC = "https://zoek.officielebekendmakingen.nl/{}.xml"
UA = "mapscale-research/0.1 (personal project; CC0 open data)"
WANT = ("tussenkop", "al")
# XML predefines only 5 entities; the source ships HTML ones, which make ET raise.
_ENT = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)([a-zA-Z]+);")
_HTML_ENT = {"nbsp": " ", "eacute": "é", "egrave": "è", "euml": "ë", "iuml": "ï",
             "ouml": "ö", "uuml": "ü", "auml": "ä", "ccedil": "ç", "ndash": "–",
             "mdash": "—", "rsquo": "’", "lsquo": "‘", "ldquo": "“", "rdquo": "”",
             "hellip": "…", "deg": "°", "euro": "€", "oacute": "ó", "aacute": "á"}

def _pre(x):
    return _ENT.sub(lambda m: _HTML_ENT.get(m.group(1), " "), x)

def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag

def _text(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()

def extract(xml):
    """Ordered [{el, text, nested_in}] over the body, plus the per-tag control counts."""
    root = ET.fromstring(_pre(xml))
    body = None
    for el in root.iter():
        if _local(el.tag) in ("tekst", "zakelijke-mededeling-tekst", "body"):
            body = el
            break
    scope = body if body is not None else root
    parent = {c: p for p in scope.iter() for c in p}
    seq = []
    for el in scope.iter():
        name = _local(el.tag)
        if name not in WANT:
            continue
        t = _text(el)
        if not t:
            continue
        anc = []
        p = parent.get(el)
        while p is not None:
            anc.append(_local(p.tag))
            p = parent.get(p)
        seq.append({"el": name, "text": t,
                    "nested_in": next((a for a in anc if a in WANT), None)})
    counts = {w: sum(1 for e in seq if e["el"] == w) for w in WANT}
    return seq, counts

async def one(client, sem, ident):
    async with sem:
        try:
            r = await client.get(DOC.format(ident), headers={"User-Agent": UA})
            if r.status_code != 200:
                return {"identifier": ident, "status": f"http_{r.status_code}"}
            xml = r.text
        except Exception as e:
            return {"identifier": ident, "status": "fetch_error", "err": type(e).__name__}
        await asyncio.sleep(0.15)
    if "</metadata>" not in xml:
        return {"identifier": ident, "status": "no_body_element"}
    try:
        seq, counts = extract(xml)
    except ET.ParseError as e:
        return {"identifier": ident, "status": "parse_error", "err": str(e)[:120]}
    return {"identifier": ident, "status": "ok",
            "url": f"https://zoek.officielebekendmakingen.nl/{ident}.html",
            "n_tussenkop": counts["tussenkop"], "n_al": counts["al"], "sequence": seq}

async def main():
    import httpx
    census = json.loads((SP / "census.json").read_text())
    out, mismatches = {}, []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        sem = asyncio.Semaphore(5)
        for dt, c in census.items():
            if c["total_tussenkop"] == 0:
                print(f"{dt:32} 0 headings -> no shard (nothing to judge)")
                continue
            shard = json.loads((SP / c["shard_file"]).read_text())
            ctrl = {r["identifier"]: r["n_tussenkop"] for r in shard["rows"]
                    if r["status"] == "ok" and r["n_tussenkop"] > 0}
            rows = await asyncio.gather(*[one(client, sem, i) for i in ctrl])
            bad = [r for r in rows if r["status"] != "ok"]
            for r in rows:
                if r["status"] == "ok" and r["n_tussenkop"] != ctrl[r["identifier"]]:
                    mismatches.append((dt, r["identifier"],
                                       ctrl[r["identifier"]], r["n_tussenkop"]))
            nh = sum(r.get("n_tussenkop", 0) for r in rows)
            name = c["shard_file"].replace("shard-", "judge-")
            (SP / name).write_text(json.dumps(
                {"doc_type": dt, "n_docs": len(rows), "n_headings": nh,
                 "control_headings": sum(ctrl.values()), "docs": rows},
                ensure_ascii=False, indent=1))
            out[dt] = {"file": name, "n_docs": len(rows), "n_headings": nh,
                       "control": sum(ctrl.values()), "non_ok": len(bad)}
            flag = "" if nh == sum(ctrl.values()) else "  <<< MISMATCH"
            print(f"{dt:32} docs {len(rows):>3}  headings {nh:>4} "
                  f"(control {sum(ctrl.values()):>4}){flag}")
    (SP / "judge-index.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\nshards {len(out)}  headings {sum(v['n_headings'] for v in out.values())} "
          f"(control {sum(v['control'] for v in out.values())})")
    if mismatches:
        print(f"\nFATAL: {len(mismatches)} per-document disagreements with the control:")
        for m in mismatches[:12]:
            print(f"  {m[0]} {m[1]}: control {m[2]} vs ET {m[3]}")
        raise SystemExit(1)
    print("per-document counts agree with the independent control for every doc.")

asyncio.run(main())
