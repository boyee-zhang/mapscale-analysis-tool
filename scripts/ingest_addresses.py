"""
Seed the nl_addresses Elasticsearch index with Dutch address data from PDOK.

Usage:
    python scripts/ingest_addresses.py                    # default seed terms
    python scripts/ingest_addresses.py --terms "Amsterdam" "Rotterdam"
    python scripts/ingest_addresses.py --city Amsterdam --rows 100

PDOK Locatieserver (free, no API key needed):
  https://api.pdok.nl/bzk/locatieserver/search/v3_1/suggest
"""

import asyncio
import argparse
import re
import httpx
from elasticsearch import AsyncElasticsearch, helpers

ES_URL = "http://localhost:9200"
INDEX = "nl_addresses"

PDOK_SUGGEST = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/suggest"

# Default seed terms — broad enough to pull diverse addresses
DEFAULT_TERMS = [
    "Hoofdstraat", "Kerkstraat", "Schoolstraat", "Molenweg",
    "Dorpsstraat", "Stationsweg", "Nieuwstraat", "Wilhelminastraat",
    "Beatrixstraat", "Julianaplein", "Markt", "Laan",
    "Amsterdam", "Rotterdam", "Utrecht", "Den Haag",
    "Eindhoven", "Groningen", "Tilburg", "Almere",
]

_WKT_POINT = re.compile(r"POINT\(([0-9.]+)\s+([0-9.]+)\)")


def parse_wkt_point(wkt: str) -> tuple[float, float] | None:
    """'POINT(4.936 52.338)' → (4.936, 52.338)"""
    m = _WKT_POINT.match(wkt or "")
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


async def fetch_pdok(client: httpx.AsyncClient, q: str, rows: int = 50) -> list[dict]:
    params = {"q": q, "fq": "type:adres", "rows": rows, "fl": "*"}
    try:
        resp = await client.get(PDOK_SUGGEST, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("response", {}).get("docs", [])
    except Exception as e:
        print(f"  [warn] PDOK request failed for '{q}': {e}")
        return []


def doc_to_es(doc: dict) -> dict | None:
    coords = parse_wkt_point(doc.get("centroide_ll", ""))
    if not coords:
        return None
    lng, lat = coords
    return {
        "_index": INDEX,
        "_id": doc.get("id"),
        "_source": {
            "street":       doc.get("straatnaam", ""),
            "housenumber":  doc.get("huis_nlt", ""),
            "postcode":     doc.get("postcode", ""),
            "city":         doc.get("woonplaatsnaam", ""),
            "municipality": doc.get("gemeentenaam", ""),
            "label":        doc.get("weergavenaam", ""),
            "location":     {"lat": lat, "lon": lng},
            "source_id":    doc.get("id", ""),
        },
    }


async def main(terms: list[str], rows: int):
    es = AsyncElasticsearch(ES_URL)

    # ensure index exists
    if not await es.indices.exists(index=INDEX):
        print(f"Index '{INDEX}' does not exist — run the backend first to create it, or create it manually.")
        await es.close()
        return

    total_indexed = 0

    async with httpx.AsyncClient() as http:
        for term in terms:
            print(f"Fetching PDOK: '{term}' ...")
            docs = await fetch_pdok(http, term, rows=rows)
            actions = [doc_to_es(d) for d in docs]
            actions = [a for a in actions if a]

            if not actions:
                print(f"  no results for '{term}'")
                continue

            success, errors = await helpers.async_bulk(es, actions, raise_on_error=False)
            total_indexed += success
            if errors:
                print(f"  {success} indexed, {len(errors)} errors")
            else:
                print(f"  {success} indexed")

    print(f"\nDone. Total indexed: {total_indexed}")
    await es.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--terms", nargs="+", default=DEFAULT_TERMS,
                        help="Search terms to query from PDOK")
    parser.add_argument("--rows", type=int, default=50,
                        help="Max results per term (PDOK caps at 100)")
    args = parser.parse_args()
    asyncio.run(main(args.terms, args.rows))
