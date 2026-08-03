"""A bare answer to the weather guard's clarify question ("tokyo" answering
"Which city's weather do you want?") used to reach the orchestrator with no
"weather" keyword, so it picked ask_research_agent (6-round scrape, 41s
live 2026-07-10) instead of ask_web_search. _rewrite_weather_followup()
rewrites it to "weather in tokyo" so the existing forced-tool machinery
handles it correctly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness import _rewrite_weather_followup
from tools.query_guard import _WEATHER_REPLY


def test_rewrites_bare_followup_after_weather_clarify():
    prior = [{"role": "assistant", "content": _WEATHER_REPLY}]
    assert _rewrite_weather_followup("tokyo", prior) == "weather in tokyo"


def test_no_rewrite_without_prior_clarify():
    prior = [{"role": "assistant", "content": "Sure, here's your calendar."}]
    assert _rewrite_weather_followup("tokyo", prior) == "tokyo"


def test_no_rewrite_when_query_already_mentions_weather():
    prior = [{"role": "assistant", "content": _WEATHER_REPLY}]
    assert _rewrite_weather_followup("weather in tokyo", prior) == "weather in tokyo"


def test_no_rewrite_with_empty_history():
    assert _rewrite_weather_followup("tokyo", []) == "tokyo"


if __name__ == "__main__":
    test_rewrites_bare_followup_after_weather_clarify()
    test_no_rewrite_without_prior_clarify()
    test_no_rewrite_when_query_already_mentions_weather()
    test_no_rewrite_with_empty_history()
    print("ok")
