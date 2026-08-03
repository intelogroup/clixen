"""
Self-check for tools/tool_failure_log.py — repeated-failure hint threshold.
"""
import os
import sys
import uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools-harness'))

from tools import tool_failure_log


def test_hint_appears_after_threshold():
    tool_name = f"test_tool_{uuid.uuid4().hex}"
    error = "[tool error] test_tool failed: widget '/some/path' not found"
    assert tool_failure_log.get_hint(tool_name) is None
    for _ in range(3):
        tool_failure_log.record_failure(tool_name, error)
    hint = tool_failure_log.get_hint(tool_name)
    assert hint is not None
    assert tool_name in hint


def test_fresh_tool_has_no_hint():
    tool_name = f"test_tool_{uuid.uuid4().hex}"
    assert tool_failure_log.get_hint(tool_name) is None
