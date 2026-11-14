"""The `nl_addresses` index: its name, its mapping, and creating it.

Moved out of upstream/es_client.py (2026-11-14), which keeps only the connection. A Dutch
stemmer over a `street` field and a `geo_point` named `location` are statements about what an
address is; transport does not get to hold them. `ensure_index` came with them for the same
reason: "create MY index with MY mapping" is a domain operation that happens to speak ES.

It had **no caller** then and has none now -- `etl/ingest/addresses.py` creates the index
itself with its own `INDEX = "nl_addresses"` constant. That duplication is recorded in
CLAUDE.md's Known Issues rather than fixed here, because removing it means either the ETL
importing from `api/` (which only four scripts do, and arguably should not) or a shared
schema package, and neither is a move.
"""
from __future__ import annotations

from ...logger import get_logger

logger = get_logger("domain.address")

INDEX = "nl_addresses"

INDEX_SETTINGS = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "filter": {
                "dutch_stop": {"type": "stop", "stopwords": "_dutch_"},
                "dutch_stemmer": {"type": "stemmer", "language": "dutch"},
            },
            "analyzer": {
                "dutch_address": {
                    "tokenizer": "standard",
                    "filter": ["lowercase", "dutch_stop", "dutch_stemmer"],
                }
            },
        },
    },
    "mappings": {
        "properties": {
            "street":      {"type": "text", "analyzer": "dutch_address"},
            "housenumber": {"type": "keyword"},
            "postcode":    {"type": "keyword"},
            "city":        {"type": "text", "analyzer": "dutch_address"},
            "municipality":{"type": "keyword"},
            "label":       {"type": "text", "analyzer": "dutch_address"},
            "location":    {"type": "geo_point"},
            "source_id":   {"type": "keyword"},
        }
    },
}


async def ensure_index(es) -> None:
    exists = await es.indices.exists(index=INDEX)
    if not exists:
        await es.indices.create(index=INDEX, body=INDEX_SETTINGS)
        logger.info("es index created", extra={"index": INDEX})
    else:
        logger.info("es index already exists", extra={"index": INDEX})
