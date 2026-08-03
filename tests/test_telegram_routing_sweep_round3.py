"""
Regression tests from a third 10-query Telegram-simulated sweep covering:
Creole/French multilingual routing, casual length constraints, URLs in filesystem
queries, OCR/multimodal routing, spotlight/macOS native, document creation, git/repl,
system status, and strong filesystem checks.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

from clients.router import classify_telegram


def test_classify_telegram_creole_french_routes_to_multilingual():
    # Haitian Creole and unaccented French keywords should trigger multilingual intent.
    _, intent1 = classify_telegram("kijan ou ye jodi a?")
    assert intent1 == "multilingual"

    _, intent2 = classify_telegram("Bonjour, comment allez-vous?")
    assert intent2 == "multilingual"


def test_classify_telegram_short_vs_long_casual_chat():
    # Under 35 characters matching casual trigger routes to casual.
    _, intent1 = classify_telegram("hello there!")
    assert intent1 == "casual"

    # Over 35 characters with casual words and no temporal cues falls through to factual_qa/search.
    _, intent2 = classify_telegram("hello my dear friend, hope you are having an absolutely wonderful day and feel happy!")
    assert intent2 == "factual_qa"


def test_classify_telegram_url_in_filesystem_query_routes_to_url_fetch():
    # A query with .txt but also containing a URL should not route to filesystem.
    _, intent = classify_telegram("read the contents of http://example.com/data.txt")
    assert intent != "filesystem"
    assert intent == "url_fetch"


def test_classify_telegram_ocr_intent_uses_multimodal_local_model():
    # OCR query should route to 'ocr' and explicitly use the local multimodal model.
    model, intent = classify_telegram("ocr this screenshot")
    assert intent == "ocr"
    assert model == "openrouter/google/gemini-2.5-flash"


def test_classify_telegram_spotlight_macos_native_intents():
    # Spotlight and macOS native tools should route correctly.
    _, intent1 = classify_telegram("find notes.txt using spotlight")
    assert intent1 == "spotlight"

    _, intent2 = classify_telegram("copy this text to my clipboard")
    assert intent2 == "macos_native"


def test_classify_telegram_document_creation():
    # Queries asking to make or export documents should route to the document intent.
    _, intent1 = classify_telegram("export this table to a PowerPoint presentation")
    assert intent1 == "document"

    _, intent2 = classify_telegram("create a docx document containing my notes")
    assert intent2 == "document"


def test_classify_telegram_git_and_repl():
    # Git and REPL command patterns should route to git and repl intents.
    _, intent1 = classify_telegram("git checkout main branch")
    assert intent1 == "git"

    _, intent2 = classify_telegram("run this code to compute fibonacci")
    assert intent2 == "repl"


def test_classify_telegram_system_status():
    # System status queries should route to system_status intent.
    _, intent = classify_telegram("what is my memory usage?")
    assert intent == "system_status"


def test_classify_telegram_strong_filesystem_prevents_temporal_hijack():
    # "right now" temporal keyword should not steal filesystem when strong FS markers exist.
    _, intent = classify_telegram("How many folders are in my desktop folder right now?")
    assert intent == "filesystem"
