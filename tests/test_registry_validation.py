"""
Self-check for tools/registry.py:execute_tool() schema validation (jsonschema-backed).
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools-harness'))

from tools.registry import execute_tool


def test_valid_args_pass_through(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    result = execute_tool("list_directory", {"path": str(tmp_path)})
    assert "[invalid arguments]" not in result
    assert "a.txt" in result


def test_missing_required_arg_rejected():
    result = execute_tool("read_file", {})
    assert result.startswith("[invalid arguments] read_file rejected:")


def test_wrong_type_rejected():
    result = execute_tool("read_file", {"path": 123})
    assert result.startswith("[invalid arguments] read_file rejected:")
