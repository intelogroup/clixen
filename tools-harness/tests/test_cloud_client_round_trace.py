from types import SimpleNamespace as N

from clients.cloud_client import _fmt_round_trace


def test_trace_names_each_tool_with_truncated_args():
    calls = [N(function=N(name="click", arguments='{"id":"e5"}')), N(function=N(name="observe", arguments=None))]
    assert _fmt_round_trace("", calls) == 'tools=click({"id":"e5"}), observe()'
    assert len(_fmt_round_trace("", [N(function=N(name="t", arguments="x" * 500))])) < 140


def test_trace_keeps_a_long_final_reply_but_caps_it():
    assert _fmt_round_trace("done", None) == "final='done'"
    assert len(_fmt_round_trace("x" * 2000, None)) < 830
    assert len(_fmt_round_trace("x" * 500, None)) > 500
