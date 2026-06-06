"""Sample custom skill: weather (placeholder - real implementation needs API key)"""


SKILL_NAME = "weather"
SKILL_DESCRIPTION = "Get current weather for a city (requires online API)"


def run(args: dict) -> str:
    """Fetch weather. Placeholder - integrate with real API in production."""
    city = args.get("city", "Beijing")
    return f"🌤️ Weather for {city}: sunny, 22°C (placeholder - integrate with real API)"
