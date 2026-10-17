"""
Housing data models.

HousingListing is the single canonical shape all providers output.
HousingProvider is the ABC every new data source must implement.

To add a new source (e.g. Funda):
    1. Create api/main/housing/providers/funda.py
    2. Subclass HousingProvider, implement the three abstract members
    3. Call registry.register(FundaProvider()) at module level
    4. Import the module in providers/__init__.py
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

ListingType = Literal["rent", "sale"]
ListingStatus = Literal["available", "lottery", "reserved", "unknown"]


@dataclass
class HousingListing:
    """
    Provider-agnostic housing listing.

    Providers populate every field they have data for.
    lat/lng are filled by the geocoder after fetch — providers must NOT set them.
    """
    id: str
    provider: str               # "h2s" | "funda" | ...
    listing_type: ListingType   # "rent" | "sale"
    name: str                   # full display name / address string
    city: str
    neighborhood: Optional[str]
    price: Optional[float]      # monthly rent (rent) OR asking price (sale), in €
    area_m2: Optional[float]
    property_type: Optional[str]    # "studio" | "apartment" | "house" | ...
    status: ListingStatus
    available_from: Optional[str]   # "YYYY-MM-DD" — move-in date (rent) or listing date (sale)
    url: str

    lat: Optional[float] = None
    lng: Optional[float] = None
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    extra: dict = field(default_factory=dict)  # provider-specific fields not in the schema

    @property
    def price_display(self) -> str:
        if self.price is None:
            return "Price unknown"
        if self.listing_type == "rent":
            return f"€{self.price:.0f}/mo"
        return f"€{self.price:,.0f}"

    @property
    def has_coords(self) -> bool:
        return self.lat is not None and self.lng is not None

    def to_geojson_feature(self) -> dict:
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [self.lng, self.lat]},
            "properties": {
                "id": self.id,
                "provider": self.provider,
                "listing_type": self.listing_type,
                "name": self.name,
                "city": self.city,
                "neighborhood": self.neighborhood,
                "price": self.price,
                "price_display": self.price_display,
                "area_m2": self.area_m2,
                "property_type": self.property_type,
                "status": self.status,
                "available_from": self.available_from,
                "url": self.url,
            },
        }


class HousingProvider(ABC):
    """
    Abstract base class for all housing data providers.

    Subclass this and implement the three abstract members.
    Geocoding is handled by the router layer — providers return raw listings only.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique slug used as query param, e.g. 'h2s'."""

    @property
    @abstractmethod
    def listing_type(self) -> ListingType:
        """'rent' or 'sale'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'Holland2Stay'."""

    @property
    @abstractmethod
    def supported_cities(self) -> list[str]:
        """City names this provider covers."""

    @abstractmethod
    async def fetch_listings(
        self,
        city_names: list[str] | None = None,
        all_statuses: bool = False,
    ) -> list[HousingListing]:
        """
        Fetch listings. city_names=None means all supported cities.
        all_statuses=True disables the availability filter so occupied/reserved
        listings are included (needed for past-window queries).
        Do not set lat/lng — the router geocodes after fetch.
        """
