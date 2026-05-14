"""
City-level housing stats: count / avg price / avg area / avg distance to central station,
grouped by property type (Studio / 1-bed / 2-bed+).
"""
from __future__ import annotations

import json
import math

from openai import AsyncOpenAI

from ..config import DEEPSEEK_API_KEY
from .models import HousingListing

# Central train station coordinates for every H2S city
CENTRAL_STATIONS: dict[str, tuple[float, float]] = {
    "Amersfoort":             (52.1528, 5.3742),
    "Amsterdam":              (52.3791, 4.9003),
    "Arnhem":                 (51.9247, 5.9027),
    "Capelle aan den IJssel": (51.9209, 4.5683),
    "Delft":                  (52.0062, 4.3560),
    "Den Bosch":              (51.6906, 5.2930),
    "Diemen":                 (52.3467, 4.9406),
    "Dordrecht":              (51.8106, 4.6744),
    "Eindhoven":              (51.4432, 5.4796),
    "Groningen":              (53.2106, 6.5636),
    "Haarlem":                (52.3877, 4.6371),
    "Helmond":                (51.4833, 5.6556),
    "Leiden":                 (52.1659, 4.4800),
    "Maarssen":               (52.1389, 5.0389),
    "Maastricht":             (50.8511, 5.7075),
    "Nieuwegein":             (52.0317, 5.0872),
    "Nijmegen":               (51.8425, 5.8523),
    "Rijswijk":               (52.0483, 4.3236),
    "Rotterdam":              (51.9249, 4.4690),
    "Sittard":                (51.0017, 5.8681),
    "The Hague":              (52.0806, 4.3239),
    "Tilburg":                (51.5614, 5.0786),
    "Utrecht":                (52.0894, 5.1099),
    "Velp":                   (51.9978, 5.9828),
    "Zeist":                  (52.0878, 5.2336),
    "Zoetermeer":             (52.0633, 4.4953),
}


def _classify_type(prop_type: str | None) -> str:
    if not prop_type:
        return "2-bed+"
    pt = prop_type.lower().strip()
    if "studio" in pt:
        return "Studio"
    if pt in ("1", "one", "1-bedroom", "1 bedroom"):
        return "1-bed"
    return "2-bed+"


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return round(2 * R * math.asin(math.sqrt(a)), 2)


def _avg(vals: list) -> float | None:
    return round(sum(vals) / len(vals), 1) if vals else None


def compute_city_stats(listings: list[HousingListing], city: str) -> dict:
    station = CENTRAL_STATIONS.get(city)
    buckets: dict[str, dict] = {
        "Studio": {"prices": [], "areas": [], "distances": []},
        "1-bed":  {"prices": [], "areas": [], "distances": []},
        "2-bed+": {"prices": [], "areas": [], "distances": []},
    }

    for lst in listings:
        b = buckets[_classify_type(lst.property_type)]
        if lst.price:
            b["prices"].append(lst.price)
        if lst.area_m2:
            b["areas"].append(lst.area_m2)
        if station and lst.has_coords:
            b["distances"].append(_haversine_km(lst.lat, lst.lng, *station))

    by_type = {}
    for label, b in buckets.items():
        count = max(len(b["prices"]), len(b["areas"]), len(b["distances"]))
        if count == 0:
            continue
        by_type[label] = {
            "count":           count,
            "avg_price":       _avg(b["prices"]),
            "avg_area_m2":     _avg(b["areas"]),
            "avg_distance_km": _avg(b["distances"]),
        }

    return {
        "city":    city,
        "total":   sum(v["count"] for v in by_type.values()),
        "by_type": by_type,
    }


async def generate_city_narrative(city: str, stats: dict) -> str:
    if not DEEPSEEK_API_KEY:
        return "AI narrative unavailable (DEEPSEEK_API_KEY not configured)."

    client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    response = await client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a housing market analyst for Holland2Stay rentals in the Netherlands. "
                    "Be concise, data-driven, and practical for someone looking to rent."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Analyze the current H2S rental market for {city}:\n\n"
                    f"{json.dumps(stats, indent=2)}\n\n"
                    f"Reply in exactly this format (no deviations):\n\n"
                    f"**Market** — [1 sentence: total listings + overall price range]\n\n"
                    f"**Best value** — [1 sentence: which type has lowest €/m² and why it matters]\n\n"
                    f"**Accessibility** — [1 sentence: which type is closest to central station and the range]\n\n"
                    f"**Tip** — [1 actionable sentence for a house-hunter in this city]"
                ),
            },
        ],
        max_tokens=300,
    )
    return response.choices[0].message.content or ""
