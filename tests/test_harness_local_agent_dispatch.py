"""
Regression tests for harness.py's filesystem-intent direct-path gate.

Covers two related bugs found while verifying the Telegram local-agent flow:
1. run_local_agent got renamed/removed but the call site referenced the old
   name, raising NameError and silently falling back to the plain LLM loop
   on every filesystem query.
2. The direct fast-path resolves to a single folder, so a query naming two
   locations ("Downloads and Google Drive") silently dropped the second one
   instead of deferring to the LangGraph agent, which can call find_files
   against each location in turn.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

import harness as m
import _harness_fs_actions as fsa


def test_filesystem_intent_calls_run_local_agent():
    with patch("harness.parse_local_fs_action", return_value=None), \
         patch("harness._specialist_dispatch", return_value=None), \
         patch("harness._guard_check", return_value=None), \
         patch("harness.run_local_agent", return_value="ok result") as mock_agent:
        result, source, intent = m.run(
            query="find a file named first aid in my Downloads and Google Drive",
            force_local_agent=True,
        )
    mock_agent.assert_called_once()
    assert result == "ok result"
    assert source == "local-agent-graph"


def test_multi_location_query_skips_direct_path():
    query = "Now can you verify my download and Google Drive to see if I have any documents named “first aid”?"
    with patch("harness._specialist_dispatch", return_value=None), \
         patch("harness._guard_check", return_value=None), \
         patch("harness.run_local_agent", return_value="ok result") as mock_agent:
        result, source, intent = m.run(query=query, force_local_agent=True)
    mock_agent.assert_called_once()
    assert source == "local-agent-graph"


def test_single_location_query_still_uses_direct_path():
    with patch("harness._guard_check", return_value=None), \
         patch("harness.run_local_agent") as mock_agent:
        result, source, intent = m.run(
            query="find files named memo in my Downloads",
            force_local_agent=True,
        )
    mock_agent.assert_not_called()
    assert source == "gemma4:12b-mlx"


def test_find_glob_pattern_strips_curly_quotes_and_punctuation():
    pattern = fsa._find_glob_pattern("documents named “first aid”?")
    assert pattern == "*first aid*"
