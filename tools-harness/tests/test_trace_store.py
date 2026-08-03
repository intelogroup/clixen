"""
tests/test_trace_store.py
Test query_traces functionality in store/trace_store.py.
"""
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest

from store import trace_store


@pytest.fixture(autouse=True)
def temp_db_path(tmp_path):
    """Override DB path to a temp file for isolation."""
    db_file = tmp_path / "test_trace_store.sqlite"
    with patch.object(trace_store, "_DB_PATH", db_file):
        yield db_file


def test_record_and_get():
    """Verify that recording traces and retrieving by run_id works."""
    trace_store.record("run-1", {"tool": "tool_a", "error": False, "elapsed": 10})
    trace_store.record("run-1", {"tool": "tool_b", "error": True, "elapsed": 20})

    trace = trace_store.get_trace("run-1")
    assert trace is not None
    assert len(trace) == 2
    assert trace[0]["tool"] == "tool_a"
    assert trace[1]["error"] is True


def test_query_traces_by_run_id():
    """Verify filtering by run_id."""
    trace_store.record("run-abc", {"tool": "tool_x", "error": False})
    trace_store.record("run-def", {"tool": "tool_y", "error": False})

    results = trace_store.query_traces(run_id="run-abc")
    assert len(results) == 1
    assert results[0]["run_id"] == "run-abc"


def test_query_traces_by_tool():
    """Verify filtering by tool name."""
    trace_store.record("run-1", {"tool": "tool_a", "error": False})
    trace_store.record("run-2", {"tool": "tool_b", "error": False})

    results = trace_store.query_traces(tool="tool_a")
    assert len(results) == 1
    assert results[0]["run_id"] == "run-1"


def test_query_traces_by_error():
    """Verify filtering by error status."""
    trace_store.record("run-no-err", {"tool": "tool_a", "error": False})
    trace_store.record("run-with-err", {"tool": "tool_b", "error": True})

    results_err = trace_store.query_traces(has_error=True)
    assert len(results_err) == 1
    assert results_err[0]["run_id"] == "run-with-err"

    results_no_err = trace_store.query_traces(has_error=False)
    assert len(results_no_err) == 1
    assert results_no_err[0]["run_id"] == "run-no-err"


def test_query_traces_limit():
    """Verify limiting results works."""
    for i in range(5):
        trace_store.record(f"run-{i}", {"tool": "tool_a", "error": False})

    results = trace_store.query_traces(limit=3)
    assert len(results) == 3
