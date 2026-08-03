"""
Regression tests for store/event_log.py + crash_watchdog.py's structured
failure detection — added because crash_watchdog.py's original regex-tail of
text logs misses any failure shape the regex doesn't already know about
(confirmed live: whatsapp_bot.py had zero logging at all, so its crash was
invisible regardless of the regex). jobs/worker.py now writes a
{ts, kind, status, detail} row at each job/workflow state transition;
crash_watchdog.py polls that table independent of log text.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_event_log_round_trip(tmp_path, monkeypatch):
    import store.event_log as event_log
    monkeypatch.setattr(event_log, "_DB_PATH", tmp_path / "event_log_test.sqlite")

    start = event_log.latest_id()
    event_log.record_event("job", "started", {"job_id": "j1", "task_name": "demo"})
    event_log.record_event("job", "failed", {"job_id": "j1", "task_name": "demo", "error": "boom"})

    events = event_log.events_since(start)
    assert len(events) == 2
    assert events[0]["status"] == "started"
    assert events[1]["status"] == "failed"
    assert events[1]["detail"]["error"] == "boom"


def test_events_since_excludes_older_events(tmp_path, monkeypatch):
    import store.event_log as event_log
    monkeypatch.setattr(event_log, "_DB_PATH", tmp_path / "event_log_test2.sqlite")

    event_log.record_event("job", "started", {"job_id": "old"})
    cutoff = event_log.latest_id()
    event_log.record_event("job", "failed", {"job_id": "new", "error": "x"})

    events = event_log.events_since(cutoff)
    assert len(events) == 1
    assert events[0]["detail"]["job_id"] == "new"


def test_check_new_events_only_alerts_on_failed_status():
    import crash_watchdog as cw

    events = [
        {"id": 1, "ts": 1.0, "kind": "job", "status": "started", "detail": {"job_id": "j1"}},
        {"id": 2, "ts": 2.0, "kind": "job", "status": "done", "detail": {"job_id": "j1"}},
        {"id": 3, "ts": 3.0, "kind": "workflow", "status": "failed", "detail": {"automation_id": "a1", "error": "boom"}},
    ]
    sent = []
    with patch.object(cw, "send_telegram", side_effect=lambda m: sent.append(m)):
        n = cw.check_new_events(events, {}, now=100.0)
    assert n == 1
    assert len(sent) == 1
    assert "workflow failed: a1" in sent[0]


def test_check_new_events_dedupes_within_debounce_window():
    import crash_watchdog as cw

    events = [{"id": 1, "ts": 1.0, "kind": "job", "status": "failed", "detail": {"job_id": "j1", "error": "boom"}}]
    last_alert = {}
    with patch.object(cw, "send_telegram") as mock_send:
        n1 = cw.check_new_events(events, last_alert, now=100.0)
        n2 = cw.check_new_events(events, last_alert, now=105.0)  # within 60s window
        n3 = cw.check_new_events(events, last_alert, now=200.0)  # outside window

    assert (n1, n2, n3) == (1, 0, 1)
    assert mock_send.call_count == 2


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
