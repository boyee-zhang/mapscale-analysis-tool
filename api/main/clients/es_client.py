from elasticsearch import AsyncElasticsearch
from ..config import ES_URL
from ..logger import get_logger

logger = get_logger("client.es")

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


def get_es() -> AsyncElasticsearch:
    return AsyncElasticsearch(ES_URL)


async def ensure_index(es: AsyncElasticsearch) -> None:
    exists = await es.indices.exists(index=INDEX)
    if not exists:
        await es.indices.create(index=INDEX, body=INDEX_SETTINGS)
        logger.info("es index created", extra={"index": INDEX})
    else:
        logger.info("es index already exists", extra={"index": INDEX})
