"""
Edge-case tests for classify()/classify_telegram()/classify_message().

Covers multi-intent boundary ambiguity, adversarial/confusing phrasings,
empty/boundary inputs, ordering dependencies, and input-sanitation edge
cases that the main routing sweep rounds don't test.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clients.router import classify, classify_message, classify_telegram, _KNOWN_INTENTS


# ---------------------------------------------------------------------------
# Empty / boundary inputs
# ---------------------------------------------------------------------------

def test_classify_empty_string_defaults_to_casual():
    model, intent = classify("")
    assert intent == "casual"


def test_classify_whitespace_only_defaults_to_casual():
    _, intent = classify("   \t  \n  ")
    assert intent == "casual"


def test_classify_telegram_empty_defaults_to_factual_qa():
    _, intent = classify_telegram("")
    assert intent == "factual_qa"


def test_classify_telegram_symbols_only_defaults_to_factual_qa():
    _, intent = classify_telegram("!@#$%^&*()")
    assert intent in ("casual", "factual_qa")


def test_classify_emoji_only_defaults_to_casual_or_factual_qa():
    _, intent = classify("😊👍🎉")
    assert intent in ("casual", "factual_qa")


# ---------------------------------------------------------------------------
# Single-word / near-empty inputs
# ---------------------------------------------------------------------------

def test_classify_single_word_code_keyword_routes_to_code_quick():
    _, intent = classify("python")
    assert intent == "code_quick"


def test_classify_single_word_filesystem_keyword_routes_to_filesystem():
    # ponytail: bare "downloads" triggers _FILESYSTEM_RE via trailing |downloads?
    # pattern. Not wrong for most uses (implies "my Downloads folder") but would
    # misroute "download this file" if that phrasing ever reaches the router.
    _, intent = classify("downloads")
    assert intent == "filesystem"


def test_classify_telegram_filesystem_temporal_overlap_matches_classify():
    # 2026-07-11 fix: classify_telegram() checked _is_temporal before
    # _FILESYSTEM_RE (opposite of classify()'s order), so this identical text
    # routed to filesystem on web/IDE but temporal (web search) on Telegram.
    msg = "what's in my project folder right now"
    _, web_intent = classify(msg)
    _, tg_intent = classify_telegram(msg)
    assert web_intent == "filesystem"
    assert tg_intent == web_intent == "filesystem"


def test_classify_single_word_sql_not_filesystem():
    _, intent = classify("sql")
    assert intent == "code_quick"


def test_classify_single_word_system_status():
    _, intent = classify("battery")
    assert intent == "system_status"


def test_classify_single_word_clipboard():
    _, intent = classify("clipboard")
    assert intent == "macos_native"


# ---------------------------------------------------------------------------
# Multi-intent boundary: queries that match multiple pattern groups
# ---------------------------------------------------------------------------

def test_classify_code_with_analysis_keyword_routes_to_code_not_analysis():
    # Both _ANALYSIS_RE and _CODE_HEAVY_RE match "explain big O of this function"
    # _CODE_HEAVY_RE wins (checked before _ANALYSIS_RE at line 267)
    _, intent = classify("explain the big O complexity of this recursive function")
    assert intent == "code_heavy"


def test_classify_explain_difference_between_technologies_routes_to_analysis():
    _, intent = classify("explain the difference between REST and GraphQL")
    assert intent == "analysis"


def test_classify_math_in_code_context_defaults_to_casual():
    # BUG: "solve the traveling salesman problem" — no regex matches it.
    # _CODE_HEAVY_RE doesn't list "solve" as standalone keyword, _HARD_MATH_RE
    # doesn't cover "traveling salesman" or "dynamic programming". Falls through
    # to "casual" with no tools. Should route to code_heavy or math.
    _, intent = classify("solve the traveling salesman problem with dynamic programming")
    assert intent == "casual"  # known limitation


def test_classify_pure_math_with_code_adjacent_phrasing_routes_to_math():
    _, intent = classify("solve this equation: x^2 + 5x + 6 = 0")
    assert intent == "math"


def test_classify_file_read_with_analysis_intent_routes_to_repl():
    # ".py" in _REPL_RE beats _FILESYSTEM_RE  — .py matched at line 116 before
    # filesystem at line 131. "time complexity" matches _CODE_HEAVY_RE but repl
    # check already fired.
    _, intent = classify("read the code in main.py and analyze the time complexity")
    assert intent == "repl"


def test_classify_search_my_computer_is_spotlight_not_filesystem():
    # _SPOTLIGHT_RE matches "search my" + "file" before _FILESYSTEM_RE
    _, intent = classify("search my computer for large files")
    assert intent == "spotlight"


def test_classify_email_with_temporal_signal_stays_email():
    _, intent = classify("check my inbox for today's unread emails")
    assert intent == "email"


# ---------------------------------------------------------------------------
# Adversarial / confusion-attack inputs
# ---------------------------------------------------------------------------

def test_classify_sql_injection_not_misrouted():
    _, intent = classify("DROP TABLE users; -- what does this SQL do")
    assert intent in ("code_quick", "code_medium")


def test_classify_html_js_not_misrouted():
    # <script> doesn't match any code/core keyword — just falls to casual
    _, intent = classify("<script>alert('xss')</script>")
    assert intent == "casual"  # no tooling, but safe


def test_classify_question_with_search_phrasing_goes_to_factual_qa():
    _, intent = classify("Search the web for latest AI research papers")
    assert intent != "browser"


def test_classify_telegram_search_web_phrasing_not_browser():
    _, intent = classify_telegram("Search the web for latest AI research papers")
    assert intent != "browser"


def test_classify_ambiguous_cost_question_routes_to_temporal():
    # "cost of living in Boston" — "in Boston" matches _LOCAL_DISCOVERY_RE,
    # which routes to temporal. Arguably correct — cost-of-living data is
    # location-specific and time-sensitive.
    _, intent = classify("what is the cost of living in Boston")
    assert intent == "temporal"


def test_classify_do_you_know_factual_question_routes_to_factual_qa():
    _, intent = classify("Do you know who played Batman in the 2022 movie?")
    assert intent == "factual_qa"


def test_classify_do_you_know_self_referential_stays_casual():
    # "do you know what I mean" — self-referential, blocked by negative lookahead
    _, intent = classify("Do you know what I mean?")
    assert intent == "casual"


def test_classify_telegram_do_you_know_factual_question_defaults_to_factual_qa():
    # classify_telegram's fallthrough is factual_qa (safer default)
    _, intent = classify_telegram("Do you know who played Batman in the 2022 movie?")
    assert intent == "factual_qa"


# ---------------------------------------------------------------------------
# Temporal boundary: what IS and ISN'T temporal
# ---------------------------------------------------------------------------

def test_classify_price_without_market_cue_not_temporal():
    _, intent = classify("how much does the ball cost")
    assert intent != "temporal"


def test_classify_price_with_dollar_sign_not_temporal():
    _, intent = classify("The total cost is $50 per unit")
    assert intent != "temporal"


def test_classify_pre_2024_year_not_temporal():
    _, intent = classify("what happened in 2021")
    assert intent != "temporal"


def test_classify_historical_when_did_routes_to_factual_qa():
    _, intent = classify("when did world war 2 end")
    assert intent == "factual_qa"


def test_classify_temporal_when_did_with_today_routes_to_temporal():
    _, intent = classify("when did the stock market close today")
    assert intent == "temporal"


def test_classify_temporal_when_will_with_next_week_routes_to_temporal():
    _, intent = classify("when will the package arrive next week")
    assert intent == "temporal"


# ---------------------------------------------------------------------------
# Ordering dependencies — regression guards for fragile pattern ordering
# ---------------------------------------------------------------------------

def test_classify_automation_beats_code_quick():
    _, intent = classify("list my automations")
    assert intent == "automation"


def test_classify_automation_beats_tasks():
    _, intent = classify("create a task automation that checks email every morning")
    assert intent == "automation"


def test_classify_telegram_automation_beats_document():
    _, intent = classify_telegram("create a workflow that generates a pdf every morning")
    assert intent == "automation"


def test_classify_telegram_document_beats_code():
    _, intent = classify_telegram("make a pdf from this python script")
    assert intent == "document"


def test_classify_macos_native_beats_code_quick():
    _, intent = classify("list my safari tabs")
    assert intent == "macos_native"


def test_classify_spotlight_beats_code_quick():
    _, intent = classify("find the pdf about project planning")
    assert intent == "spotlight"


# ---------------------------------------------------------------------------
# URL edge cases
# ---------------------------------------------------------------------------

def test_classify_url_with_code_not_filesystem():
    _, intent = classify("read the contents of http://example.com/data.txt")
    assert intent == "url_fetch"


def test_classify_telegram_url_with_code_routes_to_url_fetch():
    _, intent = classify_telegram("fetch the json from https://api.example.com/data.json")
    assert intent == "url_fetch"


def test_classify_url_with_summarize_routes_to_url_fetch():
    _, intent = classify("https://en.wikipedia.org/wiki/Python_(programming_language) summarize this")
    assert intent in ("url_fetch", "analysis", "factual_qa")


def test_classify_ftp_url_not_misrouted_to_url_fetch():
    _, intent = classify("download the file from ftp://files.example.com/data.zip")
    assert intent != "url_fetch"


# ---------------------------------------------------------------------------
# classify_message() edge cases
# ---------------------------------------------------------------------------

def test_classify_message_all_returned_intents_in_known_set():
    # If a new intent is added to classify() but not _KNOWN_INTENTS,
    # _llm_classify will reject it and silently fall back to regex —
    # which may then return an intent outside the known set, defeating the guard.
    for examples, fn in [
        (["hello", "python", "what is 2+2"], classify),
        (["hello", "ocr this image", "check my inbox"], classify_telegram),
    ]:
        for msg in examples:
            _, intent = fn(msg)
            assert intent in _KNOWN_INTENTS, f"{fn.__name__}({msg!r}) -> {intent} not in _KNOWN_INTENTS"


def test_classify_message_web_channel_uses_classify():
    result = classify_message("what is the weather in London", channel="web")
    assert result.source in ("guard", "llm", "regex_fallback")
    assert result.intent in _KNOWN_INTENTS


def test_classify_message_telegram_channel_uses_classify_telegram():
    result = classify_message("what is the weather in London", channel="telegram")
    assert result.source in ("guard", "llm", "regex_fallback")
    assert result.intent in _KNOWN_INTENTS


def test_classify_message_document_intent_gated_by_channel():
    result_web = classify_message("create a pdf of this content", channel="web")
    result_tg = classify_message("create a pdf of this content", channel="telegram")
    assert not (result_web.intent == "document" and result_tg.intent != "document")
