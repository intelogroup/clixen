"""Tests for _harness_fs_actions.count_path_tokens — the structural signal
harness.py and agents/local_agent_nodes.py use to detect 0 vs 2+ named paths."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from _harness_fs_actions import count_path_tokens


def test_none_returns_zero():
    assert count_path_tokens(None) == 0


def test_empty_string_returns_zero():
    assert count_path_tokens("") == 0


def test_path_free_query_returns_zero():
    assert count_path_tokens("what is the weather today") == 0


def test_single_path_token_counts_one():
    assert count_path_tokens("list files in ~/Downloads") == 1


def test_multiple_path_tokens_counts_all():
    assert count_path_tokens("copy ~/Downloads/a.txt to ~/Documents/b.txt") == 2
