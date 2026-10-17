"""
Housing monitor agent — powered by DeepSeek (OpenAI-compatible API).

Queries Upstash for new listings in the past N hours, then uses DeepSeek
with tool use to generate a natural-language summary with market context.
"""
from __future__ import annotations

import json

from openai import AsyncOpenAI

from ..config import DEEPSEEK_API_KEY, UPSTASH_TOKEN, UPSTASH_URL
from ..logger import get_logger
from .upstash import UpstashClient

logger = get_logger("housing.monitor")

_MODEL   = "deepseek-chat"
_BASE_URL = "https://api.deepseek.com"

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_market_context",
            "description": (
                "Get current market context for a city: total active available listings "
                "and their price range. Use this to compare new listings against the "
                "current baseline and identify if new prices are cheap or expensive."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City name exactly as it appears in the listings, e.g. 'Amsterdam'",
                    }
                },
                "required": ["city"],
            },
        },
    }
]


def _format_listings(listings: list[dict]) -> str:
    lines = []
    for lst in listings:
        price = f"€{float(lst['price']):.0f}/mo" if lst.get("price") else "price unknown"
        area  = f"{lst['area_m2']}m²" if lst.get("area_m2") else ""
        parts = [lst.get("name", lst.get("id", "?")), lst.get("city", ""), price, area]
        lines.append("• " + "  |  ".join(p for p in parts if p))
    return "\n".join(lines)


async def _execute_tool(name: str, tool_input: dict, redis: UpstashClient) -> str:
    if name == "get_market_context":
        result = await redis.get_active_city_stats(tool_input["city"])
        return json.dumps(result)
    return json.dumps({"error": f"unknown tool: {name}"})


async def run_monitor(
    target_cities: list[str] | None = None,
    hours: int = 24,
) -> dict:
    """
    Run the housing monitor agent.

    Returns:
        {
          "summary": str,
          "new_listing_count": int,
          "listings": list[dict],
        }
    """
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        raise RuntimeError("Upstash not configured")
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not configured")

    redis  = UpstashClient(UPSTASH_URL, UPSTASH_TOKEN)
    client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=_BASE_URL)

    # 1. Fetch new listings from Upstash
    all_new = await redis.get_new_listings(hours)
    logger.info("monitor fetched new listings", extra={"count": len(all_new), "hours": hours})

    # 2. Filter by target cities
    new_listings = (
        [lst for lst in all_new if lst.get("city") in target_cities]
        if target_cities else all_new
    )

    if not new_listings:
        msg = (
            f"No new listings in the past {hours} hours"
            + (f" for {', '.join(target_cities)}" if target_cities else "")
            + "."
        )
        return {"summary": msg, "new_listing_count": 0, "listings": []}

    # 3. Build initial prompt
    city_names = sorted({lst.get("city", "") for lst in new_listings if lst.get("city")})
    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "You are a housing market monitor for Holland2Stay rentals in the Netherlands. "
                "Be concise and practical. Keep summaries under 200 words."
            ),
        },
        {
            "role": "user",
            "content": (
                f"In the past {hours} hours, {len(new_listings)} new listing(s) appeared "
                f"across {len(city_names)} city/cities: {', '.join(city_names)}.\n\n"
                f"New listings:\n{_format_listings(new_listings)}\n\n"
                f"Generate a concise monitoring summary covering:\n"
                f"- How many new listings and in which cities\n"
                f"- Price range and any standout deals\n"
                f"- Comparison to current market (use get_market_context for relevant cities)\n"
                f"- Whether anything is worth flagging to a house-hunter"
            ),
        },
    ]

    # 4. Agentic loop — max 5 iterations
    for _ in range(5):
        response = await client.chat.completions.create(
            model=_MODEL,
            messages=messages,
            tools=_TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        logger.info("monitor agent turn", extra={"finish_reason": finish_reason})

        if finish_reason == "stop" or not msg.tool_calls:
            break

        # Execute tool calls
        messages.append(msg)
        for tc in msg.tool_calls:
            tool_input = json.loads(tc.function.arguments)
            result = await _execute_tool(tc.function.name, tool_input, redis)
            logger.info("monitor tool call", extra={"tool": tc.function.name, "input": tool_input})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    summary = msg.content or "No summary generated."
    return {"summary": summary, "new_listing_count": len(new_listings), "listings": new_listings}
