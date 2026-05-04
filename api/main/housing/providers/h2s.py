"""
Holland2Stay rental provider.

Fetches live listings from the H2S GraphQL API using curl_cffi to bypass
Cloudflare's TLS fingerprinting. curl_cffi is a compile-time dependency;
the provider raises ExternalServiceError cleanly if it is absent.

City IDs come from H2S's GraphQL aggregations — stable unless H2S rebuilds
their catalogue. Add new cities to CITY_IDS without touching any other file.
"""
from __future__ import annotations

import asyncio
import time
from functools import partial
from typing import Optional

from ...errors import ExternalServiceError
from ...logger import get_logger
from ..models import HousingListing, HousingProvider, ListingStatus
from ..registry import register

logger = get_logger("housing.provider.h2s")

try:
    import curl_cffi.requests as _cffi
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

_GQL_URL = "https://api.holland2stay.com/graphql/"
_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.holland2stay.com",
    "Referer": "https://www.holland2stay.com/",
    "Accept": "application/json",
}

# GraphQL category UID for Residences — fixed by H2S.
_CATEGORY_UID = "Nw=="

# availability_to_book IDs: 179 = direct booking, 336 = lottery
_AVAILABILITY_IDS = ["179", "336"]

_STATUS_MAP: dict[str, ListingStatus] = {
    "available to book": "available",
    "available in lottery": "lottery",
    "reserved": "reserved",
}

# Reverse map for extracting city name from GraphQL response (city attribute value = ID)
_ID_TO_CITY: dict[str, str] = {}  # populated after CITY_IDS is defined

CITY_IDS: dict[str, str] = {
    "Amersfoort":             "6249",
    "Amsterdam":              "24",
    "Arnhem":                 "320",
    "Capelle aan den IJssel": "619",
    "Delft":                  "26",
    "Den Bosch":              "28",
    "Diemen":                 "110",
    "Dordrecht":              "620",
    "Eindhoven":              "29",
    "Groningen":              "545",
    "Haarlem":                "616",
    "Helmond":                "6099",
    "Leiden":                 "6293",
    "Maarssen":               "6209",
    "Maastricht":             "6090",
    "Nieuwegein":             "6051",
    "Nijmegen":               "6217",
    "Rijswijk":               "6224",
    "Rotterdam":              "25",
    "Sittard":                "6211",
    "The Hague":              "90",
    "Tilburg":                "6093",
    "Utrecht":                "27",
    "Velp":                   "6265",
    "Zeist":                  "6145",
    "Zoetermeer":             "6088",
}

_ID_TO_CITY.update({v: k for k, v in CITY_IDS.items()})

_GQL_QUERY = """
{{
  products(
    filter: {{
      category_uid: {{ eq: "{category_uid}" }}
      city: {{ in: [{city_in}] }}
      {avail_filter}
    }},
    pageSize: 100,
    currentPage: {page}
  ) {{
    total_count
    page_info {{ current_page total_pages }}
    items {{
      name
      url_key
      price_range {{ minimum_price {{ regular_price {{ value }} }} }}
      custom_attributesV2 {{
        items {{
          code
          ... on AttributeValue {{ value }}
          ... on AttributeSelectedOptions {{
            selected_options {{ label value }}
          }}
        }}
      }}
    }}
  }}
}}
"""

_RELEVANT_ATTRS = {
    "available_startdate", "available_to_book", "basic_rent",
    "city", "living_area", "neighborhood", "no_of_rooms",
}


def _parse_attrs(raw: list[dict]) -> dict:
    result = {}
    for a in raw:
        code = a.get("code")
        if code not in _RELEVANT_ATTRS:
            continue
        if "value" in a and a["value"] is not None:
            result[code] = a["value"]
        elif "selected_options" in a:
            result[code] = a["selected_options"]
    return result


def _first_label(v) -> Optional[str]:
    if isinstance(v, list) and v:
        return v[0]["label"]
    return v


def _to_listing(item: dict, city_name: Optional[str] = None) -> Optional[HousingListing]:
    try:
        url_key = item.get("url_key") or item.get("sku", "")
        attrs = _parse_attrs(item.get("custom_attributesV2", {}).get("items", []))

        # Resolve city: caller may pass it explicitly (city filter path) or we
        # extract it from the GraphQL response (batch path).
        if city_name is None:
            city_attr = attrs.get("city")
            if isinstance(city_attr, list) and city_attr:
                # selected_options: [{label: "Amsterdam", value: "24"}, ...]
                city_name = city_attr[0].get("label") or _ID_TO_CITY.get(city_attr[0].get("value", ""), "Unknown")
            else:
                city_name = "Unknown"

        atb = attrs.get("available_to_book")
        raw_status = _first_label(atb) or "unknown"
        status = _STATUS_MAP.get(raw_status.lower(), "unknown")

        rent_raw = attrs.get("basic_rent")
        if rent_raw:
            price = float(rent_raw)
        else:
            try:
                price = item["price_range"]["minimum_price"]["regular_price"]["value"]
            except (KeyError, TypeError):
                price = None

        avail_date = attrs.get("available_startdate")
        available_from = avail_date.split(" ")[0] if avail_date else None

        area_raw = attrs.get("living_area")
        try:
            area_m2 = float(area_raw) if area_raw else None
        except ValueError:
            area_m2 = None

        neighborhood = attrs.get("neighborhood") or None
        property_type = _first_label(attrs.get("no_of_rooms"))

        return HousingListing(
            id=url_key,
            provider="h2s",
            listing_type="rent",
            name=item.get("name") or url_key,
            city=city_name,
            neighborhood=neighborhood,
            price=price,
            area_m2=area_m2,
            property_type=property_type,
            status=status,
            available_from=available_from,
            url=f"https://www.holland2stay.com/residences/{url_key}.html",
        )
    except Exception as e:
        logger.warning("failed to parse listing", extra={"error": str(e)})
        return None


def _scrape_all_sync(city_tasks: list[tuple[str, str]], all_statuses: bool = False) -> list[HousingListing]:
    """
    Single paginated GraphQL query for all requested cities.

    Batching all city IDs into one request avoids per-city 429 rate limiting.
    City name is extracted from each item's `city` custom attribute.
    Runs in a thread pool — curl_cffi Sessions are not async-compatible.
    all_statuses=True removes the availability filter to include occupied/reserved listings.
    """
    city_in = ", ".join(f'"{cid}"' for _, cid in city_tasks)
    _avail_ids = ", ".join(f'"{a}"' for a in _AVAILABILITY_IDS)
    avail_filter = "" if all_statuses else f"available_to_book: {{ in: [{_avail_ids}] }}"

    with _cffi.Session(impersonate="chrome110") as session:
        listings: list[HousingListing] = []
        page = 1

        while True:
            query = _GQL_QUERY.format(
                category_uid=_CATEGORY_UID,
                city_in=city_in,
                avail_filter=avail_filter,
                page=page,
            )
            # Retry up to 3 times on 429 with exponential backoff
            for attempt in range(3):
                resp = session.post(_GQL_URL, json={"query": query}, headers=_HEADERS, timeout=30)
                if resp.status_code != 429:
                    break
                wait = int(resp.headers.get("Retry-After", 10 * (2 ** attempt)))
                logger.warning("h2s 429 rate limited", extra={"page": page, "wait_s": wait})
                time.sleep(wait)
            resp.raise_for_status()
            data = resp.json()

            products = data.get("data", {}).get("products", {})
            for item in products.get("items") or []:
                listing = _to_listing(item)  # city resolved from response attrs
                if listing:
                    listings.append(listing)

            page_info = products.get("page_info", {})
            if page >= page_info.get("total_pages", 1):
                break
            page += 1

        logger.info("h2s batch scraped", extra={"cities": len(city_tasks), "total": len(listings)})
        return listings


class H2SProvider(HousingProvider):
    """Holland2Stay rental listings via GraphQL + curl_cffi."""

    @property
    def provider_id(self) -> str:
        return "h2s"

    @property
    def listing_type(self):
        return "rent"

    @property
    def display_name(self) -> str:
        return "Holland2Stay"

    @property
    def supported_cities(self) -> list[str]:
        return list(CITY_IDS.keys())

    async def fetch_listings(
        self, city_names: list[str] | None = None, all_statuses: bool = False
    ) -> list[HousingListing]:
        if not _AVAILABLE:
            raise ExternalServiceError("h2s", 503, "curl_cffi not installed — add it to requirements-local.txt")

        cities = city_names or self.supported_cities
        city_tasks = [(name, CITY_IDS[name]) for name in cities if name in CITY_IDS]

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(_scrape_all_sync, city_tasks, all_statuses))


register(H2SProvider())
