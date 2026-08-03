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
from clients.cloud_client import _StreamLeakFilter


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


if __name__ == "__main__":
    test_leak_marker_in_single_chunk_is_stripped()
    test_leak_marker_split_across_chunks_is_stripped()
    test_normal_text_passes_through_unmodified()
    test_flush_with_empty_buffer_emits_nothing()
    test_hybrid_dsml_xml_tags_fully_stripped()
    print("ok")
