"""
Regression test for the voice-routing bug found 2026-07-01: handle_voice transcribed the
audio then called _tts_and_send() directly, skipping handle_text's screenshot/email fast
paths entirely. A spoken "take a screenshot and send it to me" never actually took a
screenshot — it was classified as a normal chat/OCR request instead.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

import telegram_bot as tb


def test_handle_voice_routes_through_shared_dispatch_not_tts_and_send_directly():
    src = inspect.getsource(tb.handle_voice)
    assert "_dispatch_query(" in src
    assert "_tts_and_send(" not in src


def test_handle_text_routes_through_shared_dispatch():
    src = inspect.getsource(tb.handle_text)
    assert "_dispatch_query(" in src
