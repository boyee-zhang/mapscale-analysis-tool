"""The query encoder: one short forward pass, loaded once, shared.

WHY THE MODEL LIVES BESIDE THE API. Embedding a QUERY is one sequence; embedding the
DOCUMENTS a query matches would be a few thousand, and that asymmetry is the entire reason
document vectors are precomputed (`etl/derive/embeddings.py`) while query vectors are
not. One forward pass is cheap enough to do in the request, and doing it in-process avoids a
network hop and a second thing to deploy.

WHY IT IS OPTIONAL. `sentence-transformers` and torch are ~1.1 GB installed, which is a real
cost for anyone who only wants the map. They live in `requirements-local.txt` and this module
degrades exactly like `metrics.py` does without `prometheus_client`: `available()` goes false,
the endpoint answers 503 and says why. **Not an empty result** -- "we cannot answer" and "there
is nothing here" are different, and must not share a branch.

THE PREFIXES ARE LOAD-BEARING. e5 is trained with asymmetric prefixes: documents are encoded
as `passage: …` and queries as `query: …`. Mixing them produces no error at all and silently
degrades every ranking, so the document side lives in `etl/derive/embeddings.py` and the
query side here, with both named in the other's comments rather than retyped from memory.
"""
from __future__ import annotations

import threading

from ...logger import get_logger

logger = get_logger("domain.search")

MODEL = "intfloat/multilingual-e5-base"
DIM = 768
QUERY_PREFIX = "query: "
MAX_CHARS = 512

_model = None
_lock = threading.Lock()
_load_error: str | None = None


def available() -> bool:
    """True if the encoder can be used. Does not load the model."""
    if _model is not None:
        return True
    if _load_error is not None:
        return False
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def reason() -> str:
    return _load_error or (
        "sentence-transformers is not installed (it lives in requirements-local.txt)")


def _load():
    """Load once, under a lock.

    The lock only became load-bearing when `encode()` moved to a worker thread (2026-11-14).
    Before that, every caller ran on the single event-loop thread, so two requests could
    never be inside this function at the same time and the lock never contended -- it
    described a race that could not happen. Now two concurrent first requests really can
    arrive on different threads, and without it each would build a ~440 MB model, the second
    winning while the first's memory stayed allocated for the life of the process.
    """
    global _model, _load_error
    with _lock:
        if _model is not None or _load_error is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("loading query encoder", extra={"model": MODEL})
            _model = SentenceTransformer(MODEL)
            logger.info("query encoder ready", extra={"model": MODEL, "dim": DIM})
        except Exception as e:
            _load_error = f"{type(e).__name__}: {e}"
            logger.error("query encoder failed to load", extra={"error": _load_error})


def warm() -> bool:
    """Load the model now, off the request path. Returns True if it is ready.

    Called from the app lifespan. Loading inside the first request froze the whole worker
    for ~10 s (measured 2026-11-14: 9.76 s load + 925 ms for the first forward pass), and
    with `--workers 1` that is every in-flight request, `/health` included. uvicorn does not
    accept connections until the lifespan yields, so paying it here turns a 10 s stall in
    production traffic into 10 s of startup -- inside the deploy healthcheck's
    `start_period: 40s`.

    Never raises: `_load` records the failure in `_load_error` and `/search/notices` then
    answers 503 with the reason, which is the existing contract for "no source".
    """
    if not available():
        logger.info("query encoder not installed, skipping warmup", extra={"reason": reason()})
        return False
    _load()
    return _model is not None


def encode(query: str) -> list[float] | None:
    """The query as a unit vector, or None if the encoder is unavailable.

    BLOCKING, and deliberately left so: it is a torch forward pass, 34-74 ms steady state
    on this machine. Call it with `asyncio.to_thread` from anything async -- never directly
    on the event loop, where it stalls every other request for that long.
    """
    if _model is None:
        _load()
    if _model is None:
        return None
    vec = _model.encode([QUERY_PREFIX + query[:MAX_CHARS]],
                        normalize_embeddings=True)[0]
    return [float(x) for x in vec]
