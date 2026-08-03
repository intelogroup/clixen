"""Self-check for brabble_hook.py's _clean_for_tts().

2026-07-10: _clean_for_tts() took only the first '\\n'-delimited line of any
text containing a newline. This is harmless for single-line fillers/
confirmation prompts (never contain '\\n'), but _run_harness()'s blocking
fallback (used whenever /chat/stream fails) passes the FULL multi-paragraph
reply through this same function via _speak() -- a real reply like
"Sure!\\n\\nHere's the forecast: ..." was silently cut down to "Sure!",
dropping the entire answer. Fixed by collapsing newlines to spaces instead
of truncating at the first one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brabble_hook import _clean_for_tts, MAX_TTS_CHARS, _wrap_for_kokoro, _KOKORO_MAX_CHARS


def test_multiline_reply_is_not_truncated_to_first_line():
    reply = "Sure!\n\nHere's the Boston forecast: sunny, high of 75."
    clean = _clean_for_tts(reply)
    assert "Boston forecast" in clean
    assert "sunny" in clean


def test_single_line_filler_unaffected():
    assert _clean_for_tts("Let me check.") == "Let me check."


def test_markdown_and_emoji_still_stripped():
    clean = _clean_for_tts("**Bold** _italic_ `code` 🌡️ done")
    assert "*" not in clean and "🌡️" not in clean
    assert "Bold" in clean and "done" in clean


def test_empty_text_returns_empty():
    assert _clean_for_tts("") == ""
    assert _clean_for_tts("   \n  ") == ""


def test_long_multiline_reply_still_capped_at_max_chars():
    reply = "\n\n".join(f"Sentence number {i} is here." for i in range(80))
    clean = _clean_for_tts(reply)
    assert len(clean) <= MAX_TTS_CHARS


def test_wrap_for_kokoro_caps_every_piece():
    # unpunctuated run (no '.!?\n') — sentence_boundary() would hand this
    # through as one chunk; _wrap_for_kokoro must still split it.
    text = " ".join(f"word{i}" for i in range(200))
    pieces = _wrap_for_kokoro(text)
    assert len(pieces) > 1
    assert all(len(p) <= _KOKORO_MAX_CHARS for p in pieces)
    assert " ".join(pieces) == text


def test_wrap_for_kokoro_single_short_piece_unsplit():
    assert _wrap_for_kokoro("short reply here") == ["short reply here"]


def test_wrap_for_kokoro_empty_input():
    assert _wrap_for_kokoro("") == []


if __name__ == "__main__":
    test_multiline_reply_is_not_truncated_to_first_line()
    test_single_line_filler_unaffected()
    test_markdown_and_emoji_still_stripped()
    test_empty_text_returns_empty()
    test_long_multiline_reply_still_capped_at_max_chars()
    test_wrap_for_kokoro_caps_every_piece()
    test_wrap_for_kokoro_single_short_piece_unsplit()
    test_wrap_for_kokoro_empty_input()
    print("ok")
