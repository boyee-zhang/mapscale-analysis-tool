"""Score the event-date extractor against a hand-labelled fold.

Four numbers, because they answer four different questions and one of them hides the others:

  precision     of the dates returned, how many are right
  recall        of the dates that exist, how many were found
  null rate     how often nothing is returned -- the honest-silence rate, which a high
                precision can be bought with, so it has to be printed beside it
  hallucination of the dates returned, how many are an ADMINISTRATIVE date dressed as an
                event date. Separated from "wrong" on purpose: being a day out on a range is
                a different failure from publishing the day the objection window closes.

Usage:  .venv/bin/python scripts/experiments/event_date_eval.py docs/pg-notes/raw/<fold>.tsv
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import date

import asyncpg
from dotenv import dotenv_values

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from event_dates import classify, detect, extract  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DATABASE_URL = dotenv_values(ROOT / ".env").get("DATABASE_URL")


def load(p: pathlib.Path) -> dict[str, date | None]:
    gold = {}
    for ln in p.read_text().splitlines():
        parts = ln.split("\t")
        if parts and parts[0].strip():
            gold[parts[0]] = date.fromisoformat(parts[1]) if len(parts) > 1 and parts[1] else None
    return gold


def _is_admin_date(title, body, got) -> bool:
    """Was the returned date one this document itself marks as administrative?"""
    import re
    text = re.sub(r"\s+", " ", f"{title or ''} | {body or ''}")
    return any(c.value == got and c.kind == "admin" for c in (classify(x) for x in detect(text)))


async def main(path: str) -> int:
    gold = load(pathlib.Path(path))
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch(
        "SELECT b.identifier, b.gemeente, b.title, bb.body_text FROM bekendmaking_body bb "
        "JOIN bekendmaking b USING (identifier) WHERE b.identifier = ANY($1::text[])",
        list(gold))
    await conn.close()

    tp = wrong = halluc = fp = fn = tn = 0
    errs = []
    for r in rows:
        g, res = gold[r["identifier"]], extract(r["title"], r["body_text"])
        got = res["start"]
        if g is None and got is None:
            tn += 1
        elif g is None:
            fp += 1
            errs.append(("invented", r["gemeente"], g, got, res["basis"]))
        elif got is None:
            fn += 1
            errs.append(("missed", r["gemeente"], g, got, res["basis"]))
        elif got == g:
            tp += 1
        else:
            wrong += 1
            tag = "HALLUC-admin" if _is_admin_date(r["title"], r["body_text"], got) else "off"
            if tag.startswith("HALLUC"):
                halluc += 1
            errs.append((tag, r["gemeente"], g, got, res["basis"]))

    n = len(rows)
    given = tp + wrong + fp
    has = sum(1 for v in gold.values() if v)
    print(f"{pathlib.Path(path).stem}: {n} publications, {has} with a real event date")
    print(f"  precision      {tp}/{given} = {tp / given:.1%}" if given else "  precision   n/a")
    print(f"  recall         {tp}/{has} = {tp / has:.1%}" if has else "  recall      n/a")
    print(f"  null rate      {(fn + tn)}/{n} = {(fn + tn) / n:.1%}"
          f"   (of which {tn} correct silence, {fn} missed)")
    print(f"  hallucination  {halluc + fp}/{given} = {(halluc + fp) / given:.1%}"
          f"   (admin date served as an event date, + {fp} invented)" if given else "")
    if errs:
        print("\n  errors")
        for kind, gem, g, got, basis in errs:
            print(f"    {kind:12} {str(gem)[:22]:24} gold={g} got={got} basis={basis}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1])))
