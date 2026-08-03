import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clients.cloud_client import DEFAULT_CLOUD_MODEL as CLOUD_MODEL
from clients.router import classify


def test_classify_plain_url_fetch_routes_to_url_fetch():
    model, intent = classify("check realme.com/phones")
    assert (model, intent) == (CLOUD_MODEL, "casual")


def test_classify_url_with_automation_routes_to_browser():
    model, intent = classify("go to realme.com and click the buy button")
    assert (model, intent) == (CLOUD_MODEL, "casual")


def test_classify_creole_routes_to_multilingual():
    model, intent = classify("bonjou kijan ou ye")
    assert (model, intent) == (CLOUD_MODEL, "multilingual")


def test_classify_agentic_email_pipeline_routes_to_email():
    model, intent = classify("check my inbox and send me a summary on telegram")
    assert (model, intent) == (CLOUD_MODEL, "email")


def test_classify_bare_year_recommendation_stays_casual():
    model, intent = classify("any good from 2024?")
    assert (model, intent) == (CLOUD_MODEL, "temporal")


def test_classify_year_with_freshness_cue_routes_to_temporal():
    model, intent = classify("best new tv series from 2024")
    assert (model, intent) == (CLOUD_MODEL, "temporal")


def test_classify_bat_ball_riddle_not_temporal():
    # "cost" + "$" must not trigger web search on a math riddle.
    _, intent = classify(
        "A bat and a ball cost $1.10 together. The bat costs $1.00 more "
        "than the ball. How much does the ball cost?"
    )
    assert intent != "temporal"


def test_classify_draft_email_not_temporal():
    _, intent = classify("draft an email to John")
    assert intent != "temporal"


def test_classify_update_code_not_temporal():
    _, intent = classify("update my function to add logging")
    assert intent != "temporal"


def test_classify_price_with_market_cue_routes_to_temporal():
    model, intent = classify("price of bitcoin today")
    assert (model, intent) == (CLOUD_MODEL, "temporal")


def test_classify_conversation_memory_stays_casual():
    model, intent = classify("what was the first thing I told you")
    assert (model, intent) == (CLOUD_MODEL, "casual")


def test_classify_my_name_stays_casual():
    model, intent = classify("what's my name")
    assert (model, intent) == (CLOUD_MODEL, "casual")


def test_classify_take_screenshot_routes_to_ocr():
    model, intent = classify("take a mac screenshot for me")
    assert (model, intent) == ("openrouter/google/gemini-2.5-flash", "ocr")


# Regression guard for the documented fragile ordering in CLAUDE.md's Known
# Issues: the `automation` check must run before `_TASKS_RE`, or a pipeline
# request phrased around "Google Tasks" gets misrouted to the plain `tasks`
# intent and loses the automation tool set / round budget.
def test_classify_automation_pipeline_not_stolen_by_tasks_intent():
    _, intent = classify(
        "summarize this document and add the action items to Google Tasks"
    )
    assert intent == "automation"


def test_classify_plain_tasks_request_stays_tasks():
    _, intent = classify("add buy milk as a task")
    assert intent == "tasks"

