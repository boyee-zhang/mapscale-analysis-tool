"""Embed publications into `bekendmaking_embedding` for semantic search.

    python3 -m venv /tmp/embvenv
    /tmp/embvenv/bin/pip install sentence-transformers==3.4.1 asyncpg python-dotenv
    /tmp/embvenv/bin/python scripts/embed_bekendmaking.py            # only what is missing/stale
    /tmp/embvenv/bin/python scripts/embed_bekendmaking.py --all      # re-embed everything

INCREMENTAL BY TEXT HASH, and that is the point of the design. `bekendmaking_activity` is a
VIEW: `activity_text` is reassembled from body fields every time it is read, so it CHANGES
when a body is fetched or an extraction pattern improves. A vector computed from the older
text is stale in a way nothing downstream would reveal -- the arithmetic still works and the
neighbours are quietly wrong. Storing the sha1 of the exact text encoded makes that
detectable, so a re-run embeds precisely the rows whose source moved.

This is not hypothetical: `scripts/score_noise_embedding.py` caches vectors to
`data/noise_emb_cache.npz` keyed on identifier ALONE. Those were computed before the national
body fetch, which turned `activity_text` for 34,481 event publications from a title fallback
into the real description. The cache cannot say which of its rows are still valid, so it is
NOT reused here -- the first run re-embeds the corpus, and every run after is incremental.
A cache without a content key can only be trusted or thrown away, and throwing it away once
is cheaper than being wrong about it indefinitely.

The encoding must match what the model was trained for and what queries will use: documents
are `"passage: " + text[:1800]`, normalized; a QUERY uses `"query: "`. Mixing the two degrades
retrieval with no error anywhere, which is why both sides live in `api/main/domains/search/`
constants rather than being retyped.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import pathlib
from datetime import datetime, timezone

import asyncpg
from dotenv import dotenv_values
from sentence_transformers import SentenceTransformer

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATABASE_URL = dotenv_values(ROOT / ".env").get("DATABASE_URL")

MODEL = "intfloat/multilingual-e5-base"
MAX_CHARS = 1800
PASSAGE_PREFIX = "passage: "
BATCH = 64

SELECT_ALL = """
SELECT a.identifier, a.activity_text
FROM bekendmaking_activity a
WHERE a.activity_text <> ''
ORDER BY a.identifier
"""

UPSERT = """
INSERT INTO bekendmaking_embedding (identifier, model, embedding, text_sha1, embedded_at)
SELECT * FROM unnest($1::text[], $2::text[], $3::vector[], $4::text[], $5::timestamptz[])
ON CONFLICT (identifier) DO UPDATE SET
    model = EXCLUDED.model, embedding = EXCLUDED.embedding,
    text_sha1 = EXCLUDED.text_sha1, embedded_at = EXCLUDED.embedded_at
"""


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def encode_text(text: str) -> str:
    return PASSAGE_PREFIX + (text or "")[:MAX_CHARS]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--all", action="store_true", help="re-embed even unchanged rows")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is not set in .env")

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(SELECT_ALL)
        have = {r["identifier"]: (r["model"], r["text_sha1"]) for r in await conn.fetch(
            "SELECT identifier, model, text_sha1 FROM bekendmaking_embedding")}
        todo = []
        for r in rows:
            h = sha1(encode_text(r["activity_text"]))
            prev = have.get(r["identifier"])
            if args.all or prev is None or prev != (args.model, h):
                todo.append((r["identifier"], r["activity_text"], h))
        stale = sum(1 for r in rows
                    if (p := have.get(r["identifier"])) is not None
                    and p != (args.model, sha1(encode_text(r["activity_text"]))))
        print(f"{len(rows)} publication(s) with text; {len(have)} already embedded")
        print(f"  to embed: {len(todo)}  (missing {len(todo) - stale}, stale {stale})")
        if args.limit:
            todo = todo[:args.limit]
        if not todo:
            print("nothing to do")
            return 0

        model = SentenceTransformer(args.model)
        done = 0
        for i in range(0, len(todo), 2048):
            chunk = todo[i:i + 2048]
            vecs = model.encode([encode_text(t) for _, t, _ in chunk],
                                batch_size=BATCH, normalize_embeddings=True,
                                show_progress_bar=False)
            now = datetime.now(timezone.utc)
            await conn.execute(
                UPSERT,
                [c[0] for c in chunk],
                [args.model] * len(chunk),
                ["[" + ",".join(f"{x:.6f}" for x in v) + "]" for v in vecs],
                [c[2] for c in chunk],
                [now] * len(chunk),
            )
            done += len(chunk)
            print(f"  {done}/{len(todo)}", flush=True)

        print(dict(await conn.fetchrow(
            "SELECT count(*) embedded, count(DISTINCT model) models FROM bekendmaking_embedding")))
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
