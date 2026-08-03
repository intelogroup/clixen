"""Location-less weather ("what's the weather") used to force repeated
web_search rounds trying to disambiguate (26.8s first-token, live 2026-07-10).
No user-location config exists to default to, so query_guard.check_all()
short-circuits to a clarifying question instead of guessing — same shared,
zero-latency, pre-dispatch guard every caller (harness.py orchestrator,
chat_ui.py /search/stream) already routes through.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.query_guard import check_all


def test_no_location_asks_for_city():
    for q in ["what's the weather", "what is the weather", "weather", "hows the weather today"]:
        assert check_all(q) == "Which city's weather do you want?", q


def test_with_location_passes_through():
    for q in ["whats the weather in paris right now", "weather in tokyo", "weather for tomorrow"]:
        assert check_all(q) is None, q


def test_compound_query_with_weather_clause_not_short_circuited():
    # 2026-07-11: a shopping-list request plus a trailing location-less weather
    # clause used to get discarded entirely by this guard (see comment above
    # _WEATHER_MAX_WORDS in query_guard.py).
    q = (
        "add milk and eggs to my shopping list, actually scratch eggs, "
        "I meant bread, and also what's the weather like tomorrow"
    )
    assert check_all(q) is None, q


if __name__ == "__main__":
    test_no_location_asks_for_city()
    test_with_location_passes_through()
    test_compound_query_with_weather_clause_not_short_circuited()
    print("ok")
