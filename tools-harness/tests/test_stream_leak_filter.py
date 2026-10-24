"""Live 2026-07-10: "should I bring an umbrella" streamed raw
<｜｜DSML｜｜tool_calls> tokens to the user mid-answer. _strip_leak_artifacts
only ran on the fully-aggregated content after the stream ended -- on_token
fires per-chunk, live, before that scrub ever happens. _StreamLeakFilter
buffers a holdback window so a marker split across chunks is still caught
before reaching on_token.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clients.cloud_client import _LEAK_HOLDBACK, _StreamLeakFilter


def test_leak_marker_in_single_chunk_is_stripped():
    out = []
    f = _StreamLeakFilter(out.append)
    f.feed("hello <｜｜DSML｜｜tool_calls> world " + ("x" * 100))
    f.flush()
    assert "DSML" not in "".join(out)
    assert "hello" in "".join(out) and "world" in "".join(out)


def test_leak_marker_split_across_chunks_is_stripped():
    out = []
    f = _StreamLeakFilter(out.append)
    f.feed("safe text " + ("a" * 90) + " <｜｜DSML")
    f.feed("｜｜tool_calls> tail " + ("b" * 90))
    f.flush()
    assert "DSML" not in "".join(out)


def test_normal_text_passes_through_unmodified():
    out = []
    f = _StreamLeakFilter(out.append)
    for chunk in ["Hello", " there", ", how are", " you?"]:
        f.feed(chunk)
    f.flush()
    assert "".join(out) == "Hello there, how are you?"


def test_flush_with_empty_buffer_emits_nothing():
    out = []
    f = _StreamLeakFilter(out.append)
    f.flush()
    assert out == []


def test_hybrid_dsml_xml_tags_fully_stripped():
    """Live 2026-07-10: _LEAK_TOKEN_RE originally only matched the inner
    ｜｜DSML｜｜ marker, leaving bare "<tool_calls>"/"<invoke name=...>"/
    "</parameter>" tag fragments in the stream — a partial fix that still
    leaked XML-ish tool-call syntax to the user.
    """
    out = []
    f = _StreamLeakFilter(out.append)
    leak = (
        '<｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="ask_run_command">\n'
        '<｜｜DSML｜｜parameter name="command" string="true">curl -s x'
        '</parameter>\n</invoke>\n</tool_calls>'
    )
    f.feed("before " + leak + " after " + ("z" * 90))
    f.flush()
    joined = "".join(out)
    for marker in ("DSML", "tool_calls>", "invoke name", "</parameter", "｜"):
        assert marker not in joined, (marker, joined)
    assert "before" in joined and "after" in joined


def test_short_reply_is_emitted_immediately_not_at_flush():
    """Regression (measured 2026-10-24, voice path): the fixed 80-char holdback
    meant a 32-char reply produced exactly one on_token at stream close -- 2.478 s
    in, where the provider's first delta landed at 1.427 s -- so TTS could not
    start until the whole turn was finished.
    """
    out = []
    f = _StreamLeakFilter(out.append)
    f.feed("Four.")
    assert out == ["Four."]


def test_hold_is_bounded_by_the_marker_window():
    for n in (0, 1, 5, 79, 80, 81, 200):
        assert _StreamLeakFilter._hold_len("x" * n) == 0
        assert _StreamLeakFilter._hold_len("x" * n + "<") <= _LEAK_HOLDBACK


def test_prose_with_an_angle_bracket_is_delayed_not_lost():
    out = []
    f = _StreamLeakFilter(out.append)
    f.feed("if a < b then")
    # The text before the opener streams; everything from the opener is held.
    assert out == ["if a "]
    f.feed(" x" * 45)
    f.flush()
    assert "if a < b then" in "".join(out)


if __name__ == "__main__":
    test_leak_marker_in_single_chunk_is_stripped()
    test_leak_marker_split_across_chunks_is_stripped()
    test_normal_text_passes_through_unmodified()
    test_flush_with_empty_buffer_emits_nothing()
    test_hybrid_dsml_xml_tags_fully_stripped()
    test_short_reply_is_emitted_immediately_not_at_flush()
    test_hold_is_bounded_by_the_marker_window()
    test_prose_with_an_angle_bracket_is_delayed_not_lost()
    print("ok")
