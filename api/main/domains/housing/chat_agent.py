"""
Housing chat agent — natural language city search powered by DeepSeek.

Parses user query, fetches real housing data for relevant cities,
returns structured { cities, summary } for the frontend to render.
"""
from __future__ import annotations

import json

from openai import AsyncOpenAI

from ..config import DEEPSEEK_API_KEY
from ..logger import get_logger
from .stats import CENTRAL_STATIONS, apply_time_filter, compute_city_stats

logger = get_logger("housing.chat")

_MODEL    = "deepseek-chat"
_BASE_URL = "https://api.deepseek.com"

SUPPORTED_CITIES = sorted(CENTRAL_STATIONS.keys())

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_city_stats",
            "description": (
                "Get current H2S housing stats for a city: listing count, avg price and area "
                "broken down by property type (Studio / 1-bed / 2-bed+). "
                "Call this when you need data about a specific city."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "Exact city name from the supported list",
                    }
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_overview",
            "description": (
                "Get active listing count and avg price for multiple cities at once. "
                "Use this for a quick comparison across cities before giving a summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of exact city names from the supported list",
                    }
                },
                "required": ["cities"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "respond",
            "description": (
                "Give the final answer. Always call this last after gathering data. "
                "The cities list will be highlighted on the map."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Canonical city names to highlight on the map",
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "Structured summary in English. "
                            "MUST follow this exact markdown format:\n"
                            "**{City}** — {N} listing(s) available\n"
                            "- {Type}: €{price}/mo · {area}m²\n"
                            "- (repeat per type)\n"
                            "\n"
                            "{1 sentence market insight or tip.}\n"
                            "\n"
                            "Use real data from the tool results. No extra sections."
                        ),
                    },
                },
                "required": ["cities", "summary"],
            },
        },
    },
]

_SYSTEM_PROMPT = (
    "You are a housing search assistant for Holland2Stay (H2S) rentals in the Netherlands.\n"
    "The user will describe what they are looking for in any language.\n\n"
    "Your steps:\n"
    "1. Identify which cities the user wants — map any language/variant to canonical names.\n"
    "2. Call get_market_overview or get_city_stats to fetch real data.\n"
    "3. Call respond() with the matching city names and a helpful summary.\n\n"
    "Rules:\n"
    "- IMPORTANT: The summary field in respond() MUST always be written in English, regardless of the language the user used.\n"
    "- Only use city names from the supported list.\n"
    "- If the user mentions a region (e.g. Randstad, Zuid-Holland), include all relevant cities.\n"
    "- Always call respond() as your final action.\n\n"
    f"Supported cities: {', '.join(SUPPORTED_CITIES)}"
)


async def _execute_tool(name: str, args: dict, provider) -> str:
    if name == "get_city_stats":
        city = args["city"]
        try:
            listings = apply_time_filter(await provider.fetch_listings([city]))
            stats = compute_city_stats(listings, city)
            return json.dumps(stats)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    if name == "get_market_overview":
        results = {}
        for city in args.get("cities", []):
            try:
                listings = apply_time_filter(await provider.fetch_listings([city]))
                stats = compute_city_stats(listings, city)
                results[city] = {
                    "total": stats["total"],
                    "by_type": {
                        k: {"count": v["count"], "avg_price": v["avg_price"]}
                        for k, v in stats["by_type"].items()
                    },
                }
            except Exception as exc:
                results[city] = {"error": str(exc)}
        return json.dumps(results)

    return json.dumps({"error": f"unknown tool: {name}"})


async def run_chat(query: str, provider) -> dict:
    """
    Run the housing chat agent.
    Returns { cities: list[str], summary: str }
    """
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not configured")

    client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=_BASE_URL)
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": query},
    ]

    logger.info("chat agent start", extra={"query": query})

    for _ in range(6):
        response = await client.chat.completions.create(
            model=_MODEL,
            messages=messages,
            tools=_TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        logger.info("chat agent turn", extra={"finish_reason": finish_reason})

        if not msg.tool_calls:
            break

        messages.append(msg)

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            logger.info("chat tool call", extra={"tool": tc.function.name, "tool_args": args})

            if tc.function.name == "respond":
                cities = [c for c in args.get("cities", []) if c in SUPPORTED_CITIES]
                summary = args.get("summary", "No results found.")
                logger.info("chat agent respond", extra={"cities": cities})
                return {"cities": cities, "summary": summary}

            result = await _execute_tool(tc.function.name, args, provider)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    return {"cities": [], "summary": "Could not process query. Please try again."}
