"""Stratified sample of bekendmaking documents; measure tussenkop incidence.

Deterministic: no judgement here. Emits one JSON per doc_type shard plus a census.
"""
import asyncio
import json
import random
import re
import subprocess
import sys
import pathlib

OUT = pathlib.Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
PER_STRATUM = int(sys.argv[2]) if len(sys.argv) > 2 else 50
SEED = 20261112

DOC = "https://zoek.officielebekendmakingen.nl/{}.xml"
UA = "mapscale-research/0.1 (personal project; CC0 open data)"
STRATA = ["evenementenvergunning", "andere vergunning", "omgevingsvergunning",
          "verkeersbesluit of -mededeling", "exploitatievergunning", "omgevingsmelding"]

def pick(doc_type, n):
    # Dollar-quoting with padding is a trap: "$$ x $$" is the literal ' x ', which matches
    # nothing and returns an empty set that reads exactly like "this doc_type has no rows".
    assert "'" not in doc_type, doc_type
    sql = (f"select identifier from bekendmaking where doc_type = '{doc_type}' "
           f"order by md5(identifier || '{SEED}') limit {n}")
    r = subprocess.run(["docker", "exec", "mapscale-pg", "psql", "-U", "mapscale",
                        "-d", "mapscale", "-At", "-c", sql],
                       capture_output=True, text=True, check=True)
    ids = [x for x in r.stdout.strip().split("\n") if x]
    if not ids:
        raise SystemExit(f"FATAL: stratum {doc_type!r} selected 0 rows. An empty stratum is a "
                         f"bug in this query, not a finding.\nSQL: {sql}\nstderr: {r.stderr}")
    return ids

def _EL(tag, s):
    return re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", s, re.S)


def _STRIP(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h)).strip()

_UNESC = [("&amp;","&"),("&lt;","<"),("&gt;",">"),("&quot;",'"'),("&#39;","'"),("&nbsp;"," ")]
def unesc(s):
    for a, b in _UNESC:
        s = s.replace(a, b)
    return s

async def fetch(client, sem, ident):
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
    body = xml.split("</metadata>", 1)[1]
    tus = [unesc(_STRIP(h)) for h in _EL("tussenkop", body)]
    tus = [t for t in tus if t]
    als = [unesc(_STRIP(h)) for h in _EL("al", body)]
    als = [a for a in als if a]
    ch_t, ch_a = sum(map(len, tus)), sum(map(len, als))
    return {"identifier": ident, "status": "ok", "bytes": len(xml),
            "n_tussenkop": len(tus), "n_al": len(als),
            "chars_tussenkop": ch_t, "chars_al": ch_a,
            "pct_chars_lost": round(100 * ch_t / (ch_t + ch_a), 3) if (ch_t + ch_a) else 0.0,
            "tussenkop": tus}

async def main():
    import httpx
    random.seed(SEED)
    census = {}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        sem = asyncio.Semaphore(5)
        for dt in STRATA:
            idents = pick(dt, PER_STRATUM)
            rows = await asyncio.gather(*[fetch(client, sem, i) for i in idents])
            slug = re.sub(r"[^a-z]+", "-", dt.lower()).strip("-")
            (OUT / f"shard-{slug}.json").write_text(
                json.dumps({"doc_type": dt, "seed": SEED, "n_requested": len(idents),
                            "rows": rows}, ensure_ascii=False, indent=1))
            ok = [r for r in rows if r["status"] == "ok"]
            with_t = [r for r in ok if r["n_tussenkop"] > 0]
            census[dt] = {
                "n": len(rows), "ok": len(ok), "with_tussenkop": len(with_t),
                "incidence_pct": round(100 * len(with_t) / len(ok), 1) if ok else None,
                "total_tussenkop": sum(r["n_tussenkop"] for r in ok),
                "mean_pct_chars_lost_over_ok": round(
                    sum(r["pct_chars_lost"] for r in ok) / len(ok), 3) if ok else None,
                "pooled_pct_chars_lost": round(
                    100 * sum(r["chars_tussenkop"] for r in ok)
                    / max(1, sum(r["chars_tussenkop"] + r["chars_al"] for r in ok)), 3),
                "statuses": {s: sum(1 for r in rows if r["status"] == s)
                             for s in sorted({r["status"] for r in rows})},
                "shard_file": f"shard-{slug}.json",
            }
            c = census[dt]
            print(f"{dt:32} ok {c['ok']:>3}/{c['n']:<3} "
                  f"with_tussenkop {c['with_tussenkop']:>3} ({c['incidence_pct']}%)  "
                  f"headings {c['total_tussenkop']:>4}  "
                  f"pooled_chars_lost {c['pooled_pct_chars_lost']}%")
    (OUT / "census.json").write_text(json.dumps(census, ensure_ascii=False, indent=1))
    tot_ok = sum(c["ok"] for c in census.values())
    tot_t = sum(c["total_tussenkop"] for c in census.values())
    print(f"\nTOTAL ok={tot_ok}  headings={tot_t}")
    print(f"census: {OUT / 'census.json'}")

asyncio.run(main())
