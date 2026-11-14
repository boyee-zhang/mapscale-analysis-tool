"""Measure retrieval methods against the hand-labelled noise ground truth.

One query, one concept: "this publication announces a loud activity". The frame is frozen and
exhaustively labelled (migrations/0014, docs/pg-notes/noise-frame*.csv), so recall is a real
number here rather than the pooled estimate a benchmark normally has to settle for.

Three things this file is careful about, because each of them can silently invent a result:

1. CONTAMINATION. Every label was read by the same author who would write the queries, so a
   query written now is informed by the answer key. Each arm therefore declares `tuned`:
     'no'   the query predates the labels (migration 0012) or is what a user plainly types
     'dev'  hand-tuned on ams-centrum-300 while watching ams-centrum-300 numbers
   A 'dev' arm's honest number is the one on utr-binnenstad-300, which was held out and whose
   publication templates differ -- see noise-groundtruth.md. Both are printed; only one counts.

2. THE UNIVERSE. `borderline` is 445 of 954 rows and is the one subjective cut in the label
   set. Collapsing it either way would move every precision figure, so the primary universe is
   noisy vs quiet only (502 rows) and borderline is removed from the corpus AND from each
   method's output. 'wide' (borderline = not relevant) and 'lenient' (borderline = relevant)
   are printed beside it; a method that only looks good under one of the three has not won.
   `unjudgeable` is never relevant and never retrieved -- nobody knows its answer, so grading
   a method on it rewards guessing.

3. SET vs RANKING. A boolean OR query returns a set; its P/R/F1 do not depend on the order.
   AP is printed only for arms that carry a score, and `ts_rank_cd` does no
   document-frequency weighting, so its ordering within the matched set is close to
   arbitrary. Read AP for those arms as "what the matched set is worth when shuffled".
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

import asyncpg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from api.main.db import DATABASE_URL  # noqa: E402

# --------------------------------------------------------------------------------------------
# Queries.
#
# NAIVE is the query a resident types. It is not a strawman: it is the only vocabulary the
# concept has a word for, and the corpus's own answer to it is the reason this benchmark exists.
NAIVE_RE = r"geluid|lawaai|herrie|muziek|nachtwerk|decibel|\ydb\y"
NAIVE_TS = "geluid | lawaai | herrie | muziek | nachtwerk | decibel | db"

# RULES is lifted from migrations/0012_noise_impact_rule_seed.sql -- the patterns that assign
# `explicit`, `loud_activity` or `event`. Those were written from a vocabulary census, before
# any row was labelled, which is what makes this arm the one uncontaminated hand-written query.
RULES_RE = (
    r"nachtwerk|tijdstippen van uitvoering|werktijden|arbeidstijd|geluid|lawaai"
    r"|versterkte muziek|\ysloop|slopen|sloopmelding|heien|heiwerk|damwand|trillen"
    r"|asfalt|graafwerk|straatwerk|kraan|hijs|evenement|festival|kermis"
)
# The same patterns as a tsquery. Multi-word patterns become phrase operators; the regex
# alternatives that were prefixes of each other collapse, because Dutch stemming already
# folds sloop/slopen -> 'slop'. This translation is deliberately mechanical: the question
# being asked is what FTS does with the SAME vocabulary, not what a better query would do.
RULES_TS = (
    "nachtwerk | (tijdstippen <-> van <-> uitvoering) | werktijden | arbeidstijd | geluid"
    " | lawaai | (versterkte <-> muziek) | sloop | slopen | sloopmelding | heien | heiwerk"
    " | damwand | trillen | asfalt | graafwerk | straatwerk | kraan | hijs | evenement"
    " | festival | kermis"
)

# The same vocabulary with every term made a prefix. Dutch compounds the modifier onto the
# head, so `evenement:*` reaches `evenementenvergunning` and `geluid:*` reaches
# `geluidvoorschriften` -- but nothing reaches `hijskraan` from `kraan`, because there the
# loud morpheme is the HEAD and sits at the end. This arm measures exactly how much of the
# FTS deficit is recoverable by the one knob a tsquery offers.
RULES_TS_PRE = (
    "nachtwerk:* | (tijdstippen <-> van <-> uitvoering) | werktijden:* | arbeidstijd:*"
    " | geluid:* | lawaai:* | (versterkte <-> muziek) | sloop:* | heien:* | heiwerk:*"
    " | damwand:* | trillen:* | asfalt:* | graafwerk:* | straatwerk:* | kraan:* | hijs:*"
    " | evenement:* | festival:* | kermis:*"
)
NAIVE_TS_PRE = "geluid:* | lawaai:* | herrie:* | muziek:* | nachtwerk:* | decibel:* | db:*"

FIELDS = {
    # What production actually indexes today (bekendmaking_fts_tmp).
    "ta": "coalesce(f.title,'') || ' ' || coalesce(f.abstract,'')",
    # The descriptive fields, extracted per migration 0015. What a retrieval method SHOULD see.
    "act": "f.activity_text",
    # Everything the publisher wrote, boilerplate glossary included.
    "body": "coalesce(f.body_text,'')",
}


# migrations/0012 is not only a vocabulary, it is a CLASSIFIER with priorities: every
# publication lands in exactly one impact class. Reading it as a retrieval method means
# choosing which classes count as a hit, and that choice is a separate claim from the
# patterns themselves. `cls_literal` takes the three classes the migration itself calls loud.
# `cls_works` adds `undeterminable` -- the class 0012 created as the honest "the index record
# cannot answer this" default for objectvergunning / TVM / activiteit=bouwen. The patterns
# predate the labels; the decision to treat that bucket as a positive does NOT, so that arm
# is marked 'dev' however uncontaminated its vocabulary is.
CLS_LITERAL = ("explicit", "loud_activity", "event")
CLS_WORKS = (*CLS_LITERAL, "undeterminable")


@dataclass
class Arm:
    name: str
    field: str
    kind: str  # 'all' | 'regex' | 'fts' | 'rules'
    query: str = ""
    tuned: str = "no"
    note: str = ""
    extra: str = ""  # additional OR'd SQL predicate, for the rule arm's non-text fields
    classes: tuple[str, ...] = ()

    def predicate(self) -> str:
        col = FIELDS.get(self.field, "''")
        if self.kind == "all":
            return "true"
        if self.kind == "cls":
            return f"f.impact = ANY('{{{','.join(self.classes)}}}'::text[])"
        if self.kind == "regex":
            p = f"({col}) ~* $1"
        elif self.kind == "fts":
            p = f"to_tsvector('dutch', {col}) @@ to_tsquery('dutch', $1)"
        elif self.kind == "rules":
            p = f"({col}) ~* $1"
        else:
            raise ValueError(self.kind)
        return f"({p}{' OR ' + self.extra if self.extra else ''})"

    def score(self) -> str:
        if self.kind == "fts":
            col = FIELDS[self.field]
            return (f"ts_rank_cd(to_tsvector('dutch', {col}), to_tsquery('dutch', $1))")
        return "0"


# Migration 0012 matches on doc_type and on the activiteit array as well as on text, and the
# comment there records why: 24 evenement rows in a 1,117-row frame carried the answer only in
# doc_type. Dropping those two fields is how the rule arm becomes a text-only arm, which is the
# comparison that says what the extra fields are worth.
RULES_EXTRA = "f.doc_type ~* 'evenement' OR f.activiteit && ARRAY['slopen','kappen']"

ARMS: list[Arm] = [
    Arm("all", "-", "all", note="the floor: precision = base rate, recall = 1.0"),
    Arm("naive_re:ta", "ta", "regex", NAIVE_RE, note="noise words, over what production indexes"),
    Arm("naive_re:act", "act", "regex", NAIVE_RE, note="noise words, over the activity text"),
    Arm("naive_re:body", "body", "regex", NAIVE_RE, note="noise words, over the whole document"),
    Arm("naive_fts:ta", "ta", "fts", NAIVE_TS),
    Arm("naive_fts:act", "act", "fts", NAIVE_TS),
    Arm("naive_fts:body", "body", "fts", NAIVE_TS),
    Arm("rules_re:ta", "ta", "regex", RULES_RE, note="migration 0012 text patterns, as written"),
    Arm("rules_re:act", "act", "regex", RULES_RE),
    Arm("rules_re:body", "body", "regex", RULES_RE),
    Arm("rules_re+fields:ta", "ta", "regex", RULES_RE, extra=RULES_EXTRA,
        note="0012 in full: text OR doc_type OR activiteit"),
    Arm("rules_fts:ta", "ta", "fts", RULES_TS, note="the same vocabulary, as a tsquery"),
    Arm("rules_fts:act", "act", "fts", RULES_TS),
    Arm("rules_fts:body", "body", "fts", RULES_TS),
    Arm("naive_fts_pre:ta", "ta", "fts", NAIVE_TS_PRE),
    Arm("naive_fts_pre:act", "act", "fts", NAIVE_TS_PRE),
    Arm("rules_fts_pre:ta", "ta", "fts", RULES_TS_PRE, note="every term a prefix: the one knob"),
    Arm("rules_fts_pre:act", "act", "fts", RULES_TS_PRE),
    Arm("rules_fts_pre:body", "body", "fts", RULES_TS_PRE),
    Arm("cls_literal", "-", "cls", classes=CLS_LITERAL,
        note="0012's classifier, hit = the classes it calls loud"),
    Arm("cls_works", "-", "cls", classes=CLS_WORKS, tuned="dev",
        note="+ 'undeterminable': a works permit of any kind, no activity described"),
    Arm("cls_undet_only", "-", "cls", classes=("undeterminable",), tuned="dev",
        note="that bucket alone -- it describes no activity at all"),
]


UNIVERSES = {
    # name: (labels kept in the corpus, labels counted relevant)
    "strict": (("noisy", "quiet"), ("noisy",)),
    "wide": (("noisy", "borderline", "quiet"), ("noisy",)),
    "lenient": (("noisy", "borderline", "quiet"), ("noisy", "borderline")),
}

FRAME_SQL = """
WITH f AS (
    SELECT m.frame, b.identifier, b.title, b.abstract, b.doc_type, b.activiteit,
           a.activity_text, bb.body_text, l.label, nz.impact
    FROM noise_frame_member m
    JOIN bekendmaking b USING (identifier)
    JOIN bekendmaking_activity a USING (identifier)
    JOIN noise_label l USING (identifier)
    JOIN bekendmaking_noise nz USING (identifier)
    LEFT JOIN bekendmaking_body bb ON bb.identifier = b.identifier
)
SELECT f.frame, f.identifier, f.label, ({pred}) AS hit, ({score}) AS score FROM f
"""


def metrics(rows, keep, relevant):
    rows = [r for r in rows if r["label"] in keep]
    n = len(rows)
    rel = sum(1 for r in rows if r["label"] in relevant)
    tp = sum(1 for r in rows if r["hit"] and r["label"] in relevant)
    fp = sum(1 for r in rows if r["hit"] and r["label"] not in relevant)
    ret = tp + fp
    p = tp / ret if ret else float("nan")
    rec = tp / rel if rel else float("nan")
    f1 = 2 * p * rec / (p + rec) if ret and rel and (p + rec) else 0.0
    # Average precision over the ranked matched set. Unmatched documents are never retrieved,
    # so AP is capped by recall -- which is the point: a ranking cannot rescue a missed row.
    hits = sorted([r for r in rows if r["hit"]], key=lambda r: -float(r["score"] or 0))
    seen = ap = 0
    for i, r in enumerate(hits, 1):
        if r["label"] in relevant:
            seen += 1
            ap += seen / i
    ap = ap / rel if rel else float("nan")
    return dict(n=n, rel=rel, ret=ret, tp=tp, fp=fp, p=p, r=rec, f1=f1, ap=ap)


async def run(conn, arm: Arm):
    sql = FRAME_SQL.format(pred=arm.predicate(), score=arm.score())
    return await (conn.fetch(sql, arm.query) if arm.query else conn.fetch(sql))


def fmt(m, scored):
    ap = f"{m['ap']:5.3f}" if scored else "    -"
    return (f"{m['ret']:5}{m['tp']:6}{m['fp']:6}  {m['p']:6.3f}{m['r']:7.3f}{m['f1']:7.3f}"
            f"  {ap}")


async def main() -> int:
    ap_ = argparse.ArgumentParser(description=__doc__)
    ap_.add_argument("--universe", choices=[*UNIVERSES, "all"], default="all")
    ap_.add_argument("--frame", default="all", help="a frame name, 'all', or 'both' for the union")
    args = ap_.parse_args()

    if not DATABASE_URL:
        print("DATABASE_URL is not set")
        return 1
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        frames = [r["name"] for r in await conn.fetch("SELECT name FROM noise_frame ORDER BY name")]
        if not frames:
            print("noise_frame is empty; run scripts/load_noise_groundtruth.py first")
            return 1
        gap = await conn.fetchval("SELECT count(*) FROM noise_frame_unlabelled")
        if gap:
            print(f"{gap} frame member(s) are unlabelled; the frame is not exhaustive, "
                  f"so recall would be an underestimate of unknown size. Refusing to measure.")
            return 1

        results = {arm.name: await run(conn, arm) for arm in ARMS}

        groups = [(f, [f]) for f in frames] + [("BOTH BLOCKS", frames)]
        if args.frame not in ("all", "both"):
            groups = [(args.frame, [args.frame])]
        elif args.frame == "both":
            groups = [("BOTH BLOCKS", frames)]

        universes = [args.universe] if args.universe != "all" else list(UNIVERSES)

        for uni in universes:
            keep, relevant = UNIVERSES[uni]
            print(f"\n{'=' * 100}\nuniverse '{uni}': corpus = {'/'.join(keep)}, "
                  f"relevant = {'/'.join(relevant)}\n{'=' * 100}")
            for gname, gframes in groups:
                sample = metrics([r for r in results["all"] if r["frame"] in gframes],
                                 keep, relevant)
                print(f"\n{gname}   n={sample['n']}  relevant={sample['rel']}  "
                      f"base rate={sample['rel'] / sample['n']:.3f}")
                print(f"{'arm':22}{'tuned':7}{'ret':>5}{'tp':>6}{'fp':>6}{'prec':>8}"
                      f"{'recall':>7}{'F1':>7}{'AP':>7}")
                for arm in ARMS:
                    rows = [r for r in results[arm.name] if r["frame"] in gframes]
                    m = metrics(rows, keep, relevant)
                    print(f"{arm.name:22}{arm.tuned:7}{fmt(m, arm.kind == 'fts')}")
        print()
        for arm in ARMS:
            if arm.note:
                print(f"  {arm.name:22} {arm.note}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
