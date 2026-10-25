"""The demand gate: promotions come from counted calls, never from enthusiasm."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soak import tool_demand as td  # noqa: E402

LOG = """\
[tool] searxng_search({'query': 'x'})
[tool] searxng_search({'query': 'y'})
[tool] list_emails({'query': 'from:bob'})
[tool] search_gdelt_news({'query': 'fed'})
[tool] slack_search({'query': 'deploy'})
[tool] ask_email_agent({'query': 'inbox'})
[tool] send_telegram({'text': 'hi'})
[tool] observe({'app': 'Finder'})
"""


def _write(tmp_path):
    p = tmp_path / "core_stdout.log"
    p.write_text(LOG)
    return p


def test_counts_come_from_real_tool_lines(tmp_path):
    counts = td._counts(_write(tmp_path))
    assert counts["searxng_search"] == 2
    assert counts["list_emails"] == 1
    assert sum(counts.values()) == 8


def test_classification_covers_the_four_kinds():
    assert td._classify("list_emails") == "adapter"
    assert td._classify("send_telegram") == "channel"
    assert td._classify("ask_email_agent") == "subagent"
    assert td._classify("slack_search") == "site-specific"
    assert td._classify("observe") == "generic"


def test_the_threshold_is_the_only_thing_that_promotes(tmp_path):
    counts = td._counts(_write(tmp_path))
    at_19 = td.scorecard({"slack_search": 19}, {}, 20)
    at_20 = td.scorecard({"slack_search": 20}, {}, 20)
    assert "hold" in at_19[0]["verdict"] and "1 more" in at_19[0]["verdict"]
    assert at_20[0]["verdict"].startswith("PROMOTE")
    assert counts["slack_search"] == 1  # a single call never promotes


def test_a_channel_is_never_sent_to_the_adapter_queue():
    rows = td.scorecard({"send_telegram": 99}, {}, 20)
    assert rows[0]["class"] == "channel"
    assert "PROMOTE" not in rows[0]["verdict"]


def test_a_subagent_points_at_the_direct_adapter():
    rows = td.scorecard({"ask_calendar_agent": 113}, {}, 20)
    assert rows[0]["class"] == "subagent"
    assert "direct adapter" in rows[0]["verdict"]


def test_no_data_is_reported_not_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tool_demand", "--log", str(tmp_path / "missing.log")])
    assert td.main() == 2


def test_json_output_is_machine_readable(tmp_path, monkeypatch):
    log = _write(tmp_path)
    monkeypatch.setattr(sys, "argv", ["tool_demand", "--log", str(log), "--json"])
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert td.main() == 0
    payload = json.loads(buf.getvalue())
    assert payload["threshold"] == 20 and payload["rows"]


def test_the_real_log_produces_the_same_shape():
    """Guards the script against the real log format, not just the fixture."""
    log = td.LOG_DEFAULT
    if not log.exists():
        return  # CI without a log: the fixture tests above carry the contract
    out = subprocess.run(
        [sys.executable, str(Path(td.__file__)), "--json"],
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0
    assert json.loads(out.stdout)["total_calls"] > 100
