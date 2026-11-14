"""The Elasticsearch connection, and nothing else.

The `nl_addresses` index name, its Dutch-analyzer mapping and `ensure_index` moved to
domains/address/index.py (2026-11-14): a stemmer choice and a `geo_point` field are what an
address IS, which a transport module may not know. What is left is how to reach the cluster.
"""
from __future__ import annotations

from ..config import ES_URL
from ..logger import get_logger

logger = get_logger("client.es")


def get_es():
    from elasticsearch import AsyncElasticsearch
    return AsyncElasticsearch(ES_URL)
