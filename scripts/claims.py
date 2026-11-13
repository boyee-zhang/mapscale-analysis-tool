"""The one way in and out of the measurement ledger.

    python scripts/claims.py snapshot                  # probe the world, record it
    python scripts/claims.py new --topic search --text "..." [--ack 4,7]
    python scripts/claims.py verdict 8 --verdict SURVIVED --scope "..." [--facets corpus]
    python scripts/claims.py evidence 8 --command "..." --output path/to/raw.json
    python scripts/claims.py stale [--topic search]
    python scripts/claims.py show [8]

Why a single entry point
------------------------
The three rows 0021 shipped with were hand-written INSERTs and two of them contradicted
their own verdicts. Every rule this file enforces is one that a hand-written INSERT got
wrong or could not have known:

  * a verdict is anchored to the world it was reached in (verdict_world), defaulting to
    EVERY probed facet. Anchoring to nothing is legal and sometimes right, but it has to be
    asked for, because an unanchored verdict never expires.
  * `new` will not create a claim on a topic that already has stale verdicts until each one
    is named in --ack. Not a refusal and not a warning: a disposition. The ids you chose to
    leave stale are written into the new claim's context, so the choice is durable.
  * evidence hashes the file itself. 0021 can only check that a digest is PRESENT; it cannot
    check that it is the digest OF that file.

What it deliberately does not do: decide whether your verdict is true. A row saying
SURVIVED with a scope of "everything" passes every check here. The gate buys that a verdict
exists and is anchored; it does not buy that it is honest.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

VERDICTS = ("DESTROYED", "SURVIVED", "REFUSED")


# ── the world ────────────────────────────────────────────────────────────────

def digest_of(payload: dict) -> str:
    """Content address of a probe result. Sorted keys so key order cannot change the id."""
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def probe_code() -> dict:
    """HEAD, plus whether the tree is dirty.

    `dirty` is part of the payload because a dirty HEAD does not describe the code that was
    actually measured -- without it one digest would stand for every uncommitted variant.
    """
    def git(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    porcelain = git("status", "--porcelain")
    return {"head": git("rev-parse", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(porcelain),
            "dirty_files": len(porcelain.splitlines())}


async def probe_corpus(conn) -> dict:
    """Row counts plus a content digest, so two different imports of the same size differ."""
    row = await conn.fetchrow("""
        SELECT count(*) AS n, md5(string_agg(identifier, ',' ORDER BY identifier)) AS d
        FROM bekendmaking""")
    loc = await conn.fetchval("SELECT count(*) FROM bekendmaking_locatie")
    return {"bekendmaking_rows": row["n"], "bekendmaking_ids_md5": row["d"],
            "bekendmaking_locatie_rows": loc}


def probe_graph() -> dict:
    """Every profile's graph_build_date and osm_date.

    All of them, not one: the three profiles here carry three different build dates, so a
    single `graph_date` key cannot express "only driving-car was rebuilt".

    Unreachable is a payload, not an exception. It hashes differently from a reachable
    answer, so a verdict reached while ORS was down is anchored to a world that says so --
    instead of silently comparing equal to every other unreachable moment.
    """
    base = (os.getenv("ORS_BASE_URL") or "https://api.openrouteservice.org/v2").rstrip("/")
    url = f"{base}/status"
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            d = json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        return {"url": url, "reachable": False, "error": type(e).__name__ + ": " + str(e)[:120]}
    profiles = {k: {"graph_build_date": v.get("graph_build_date"), "osm_date": v.get("osm_date")}
                for k, v in sorted((d.get("profiles") or {}).items())}
    return {"url": url, "reachable": True, "engine": (d.get("engine") or {}).get("version"),
            "profiles": profiles}


async def take_snapshot(conn) -> list[tuple[str, str, bool]]:
    """Probe every facet and upsert. Returns [(facet, digest, changed)]."""
    facets = [r["name"] for r in await conn.fetch("SELECT name FROM world_facet ORDER BY name")]
    out = []
    for facet in facets:
        if facet == "code":
            payload = probe_code()
        elif facet == "corpus":
            payload = await probe_corpus(conn)
        elif facet == "graph":
            payload = probe_graph()
        else:
            raise SystemExit(
                f"facet {facet!r} is registered in world_facet but this script has no probe "
                f"for it. A facet nobody probes never reports stale -- add the probe or "
                f"delete the row.")
        d = digest_of(payload)
        before = await conn.fetchval(
            "SELECT digest FROM world_current WHERE facet = $1", facet)
        await conn.execute("""
            INSERT INTO world_snapshot (facet, digest, payload) VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (facet, digest) DO UPDATE SET last_seen_at = now()""",
            facet, d, json.dumps(payload, default=str))
        out.append((facet, d, before is not None and before != d))
    return out


# ── commands ─────────────────────────────────────────────────────────────────

async def cmd_snapshot(conn, args):
    for facet, d, changed in await take_snapshot(conn):
        print(f"  {facet:8} {d[:16]}  {'CHANGED' if changed else 'unchanged'}")


async def cmd_new(conn, args):
    if not await conn.fetchval("SELECT true FROM topic WHERE name = $1", args.topic):
        known = [r["name"] for r in await conn.fetch("SELECT name FROM topic ORDER BY name")]
        raise SystemExit(f"unknown topic {args.topic!r}. known: {', '.join(known)}\n"
                         f"adding one is an INSERT into topic, deliberately.")

    await take_snapshot(conn)
    stale = await conn.fetch(
        "SELECT * FROM verdict_stale() WHERE topic = $1 ORDER BY verdict_id", args.topic)
    acked = {int(x) for x in (args.ack or "").replace(",", " ").split() if x}
    outstanding = sorted({r["verdict_id"] for r in stale} - acked)
    if outstanding:
        print(f"{len(stale)} stale verdict(s) on topic {args.topic!r}:\n")
        for r in stale:
            print(f"  verdict {r['verdict_id']} (claim {r['claim_id']}) {r['verdict']}")
            print(f"    {r['claim_text'][:100]}")
            print(f"    facet {r['facet']}: reached under {r['reached_under'][:12]}, "
                  f"world now {r['world_now'][:12]}")
        raise SystemExit(
            f"\nnot creating the claim. Each stale verdict needs a disposition first: either\n"
            f"re-measure it and record a new verdict, or carry it forward explicitly with\n"
            f"  --ack {','.join(str(i) for i in outstanding)}\n"
            f"The ids you ack are written into the new claim's context, so the choice is\n"
            f"durable rather than a decision nobody can see you made.")

    ctx = json.loads(args.context) if args.context else {}
    if not isinstance(ctx, dict):
        raise SystemExit("--context must be a JSON object")
    if acked:
        ctx["acked_stale"] = sorted(acked)
    cid = await conn.fetchval("""
        INSERT INTO claim (topic, text, made_by, context)
        VALUES ($1, $2, $3, $4::jsonb) RETURNING id""",
        args.topic, args.text, args.by, json.dumps(ctx))
    print(f"claim {cid} opened on {args.topic!r}"
          + (f", carrying forward stale verdict(s) {sorted(acked)}" if acked else ""))


async def cmd_verdict(conn, args):
    if args.verdict not in VERDICTS:
        raise SystemExit(f"--verdict must be one of {', '.join(VERDICTS)}")
    if args.verdict != "REFUSED" and not args.scope:
        raise SystemExit(
            "DESTROYED and SURVIVED need --scope: the narrowest form the evidence does not\n"
            "break. That line is the deliverable -- it is what gets pasted into the notes\n"
            "instead of the original wording.")
    if not await conn.fetchval("SELECT true FROM claim WHERE id = $1", args.claim_id):
        raise SystemExit(f"no claim {args.claim_id}")

    await take_snapshot(conn)
    if args.facets == "none":
        chosen = []
    elif args.facets:
        chosen = [f.strip() for f in args.facets.split(",") if f.strip()]
    else:
        chosen = [r["facet"] for r in await conn.fetch("SELECT facet FROM world_current")]

    rows = await conn.fetch(
        "SELECT id, facet, digest FROM world_current WHERE facet = ANY($1::text[])", chosen)
    missing = set(chosen) - {r["facet"] for r in rows}
    if missing:
        raise SystemExit(f"no current snapshot for facet(s): {', '.join(sorted(missing))}")

    async with conn.transaction():
        vid = await conn.fetchval("""
            INSERT INTO verdict (claim_id, agent, verdict, scope)
            VALUES ($1, $2, $3, $4) RETURNING id""",
            args.claim_id, args.agent, args.verdict, args.scope)
        for r in rows:
            await conn.execute(
                "INSERT INTO verdict_world (verdict_id, snapshot_id) VALUES ($1, $2)",
                vid, r["id"])

    print(f"verdict {vid}: {args.verdict} on claim {args.claim_id}")
    if rows:
        for r in rows:
            print(f"  anchored to {r['facet']:8} {r['digest'][:16]}")
    else:
        print("  anchored to NOTHING -- this verdict can never go stale. "
              "It will show in `unanchored`.")


async def cmd_evidence(conn, args):
    sha = None
    if args.output:
        p = Path(args.output)
        if not p.is_file():
            raise SystemExit(f"{p} is not a file. Record the digest or do not name the file.")
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
    eid = await conn.fetchval("""
        INSERT INTO evidence (claim_id, command, output_path, sha256)
        VALUES ($1, $2, $3, $4) RETURNING id""",
        args.claim_id, args.command, args.output, sha)
    print(f"evidence {eid} on claim {args.claim_id}" + (f"  sha256={sha[:16]}…" if sha else ""))


async def cmd_stale(conn, args):
    await take_snapshot(conn)
    q = "SELECT * FROM verdict_stale()" + (" WHERE topic = $1" if args.topic else "")
    rows = await conn.fetch(q, *( [args.topic] if args.topic else [] ))
    if not rows:
        print("nothing stale" + (f" on topic {args.topic!r}" if args.topic else ""))
    for r in rows:
        print(f"verdict {r['verdict_id']}  claim {r['claim_id']}  [{r['topic']}]  {r['verdict']}")
        print(f"  {r['claim_text'][:110]}")
        print(f"  {r['facet']}: {r['reached_under'][:12]} -> {r['world_now'][:12]}")
    un = await conn.fetch("SELECT * FROM verdict_unanchored")
    if un:
        print(f"\n{len(un)} verdict(s) anchored to nothing, which therefore never expire:")
        for r in un:
            print(f"  verdict {r['verdict_id']}  claim {r['claim_id']}  "
                  f"[{r['topic'] or '-'}]  {r['text'][:80]}")


async def cmd_show(conn, args):
    if args.claim_id:
        c = await conn.fetchrow("SELECT * FROM claim_current WHERE id = $1", args.claim_id)
        if not c:
            raise SystemExit(f"no claim {args.claim_id}")
        print(f"claim {c['id']}  [{c['topic'] or '-'}]  status={c['status']}")
        print(f"  {c['text']}")
        print(f"  by {c['made_by']} at {c['made_at']:%Y-%m-%d %H:%M}")
        print(f"  context {json.dumps(dict(json.loads(c['context'])), sort_keys=True)}")
        for v in await conn.fetch("""
                SELECT v.*, (SELECT string_agg(s.facet || ':' || left(s.digest, 10), ' ')
                             FROM verdict_world w JOIN world_snapshot s ON s.id = w.snapshot_id
                             WHERE w.verdict_id = v.id) AS anchors
                FROM verdict v WHERE claim_id = $1 ORDER BY id""", args.claim_id):
            print(f"  verdict {v['id']} {v['verdict']} by {v['agent']}")
            print(f"    scope: {v['scope']}")
            print(f"    anchors: {v['anchors'] or 'NONE — never expires'}")
        for e in await conn.fetch(
                "SELECT * FROM evidence WHERE claim_id = $1 ORDER BY id", args.claim_id):
            print(f"  evidence {e['id']}: {e['command'][:90]}")
            print(f"    {e['output_path'] or '(no file)'}"
                  + (f"  sha256={e['sha256'][:16]}…" if e["sha256"] else ""))
        return
    for c in await conn.fetch("SELECT * FROM claim_current ORDER BY id"):
        print(f"{c['id']:>3}  [{(c['topic'] or '-'):12}] {c['status']:10} "
              f"{(c['latest_verdict'] or '-'):9} {c['text'][:70]}")


# ── wiring ───────────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("snapshot", help="probe the world and record it")

    n = sub.add_parser("new", help="open a claim")
    n.add_argument("--topic", required=True)
    n.add_argument("--text", required=True)
    n.add_argument("--by", default=os.getenv("USER", "unknown"))
    n.add_argument("--context", help="JSON object: the EXPERIMENT's parameters, not the "
                                     "world's (those are facets now)")
    n.add_argument("--ack", help="stale verdict ids you are deliberately carrying forward")

    v = sub.add_parser("verdict", help="record what was done to a claim")
    v.add_argument("claim_id", type=int)
    v.add_argument("--verdict", required=True, choices=VERDICTS)
    v.add_argument("--scope", help="the narrowest surviving form; required unless REFUSED")
    v.add_argument("--agent", default="falsifier")
    v.add_argument("--facets", help="comma-separated, or 'none'. Default: every probed facet")

    e = sub.add_parser("evidence", help="attach a command and its output")
    e.add_argument("claim_id", type=int)
    e.add_argument("--command", required=True)
    e.add_argument("--output", help="path to raw output; it is hashed here, not described")

    s = sub.add_parser("stale", help="verdicts whose world has moved")
    s.add_argument("--topic")

    sh = sub.add_parser("show", help="one claim, or all of them")
    sh.add_argument("claim_id", type=int, nargs="?")
    return p


async def main():
    args = build_parser().parse_args()
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set. Nothing can be recorded, so nothing "
                         "should be measured yet.")
    conn = await asyncpg.connect(dsn)
    try:
        await {"snapshot": cmd_snapshot, "new": cmd_new, "verdict": cmd_verdict,
               "evidence": cmd_evidence, "stale": cmd_stale, "show": cmd_show}[args.cmd](conn, args)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
