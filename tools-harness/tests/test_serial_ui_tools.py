"""Screen-mutating tool calls must never run in parallel.

Measured on info-chir's submission form: one round carried five type_text calls and seven
click_text calls with identical timestamps. Up to four ran at once, and keystrokes land in
whichever field has focus, so a form fill comes out garbled.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HARNESS = Path(__file__).resolve().parents[1]
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from clients.cloud_client import SERIAL_UI_TOOLS, tool_worker_count  # noqa: E402


def tc(name):
    return SimpleNamespace(function=SimpleNamespace(name=name))


@pytest.mark.parametrize("name", ["act", "click_text", "type_text", "scroll_page",
                                  "open_url", "workspace_write", "clipboard_write"])
def test_mutators_are_serial(name):
    assert name in SERIAL_UI_TOOLS
    assert tool_worker_count([tc(name), tc(name)]) == 1


def test_a_single_mutator_serialises_the_whole_batch():
    """A read after a click must see the clicked page, so the read waits too."""
    calls = [tc("read_page_text"), tc("act"), tc("observe")]
    assert tool_worker_count(calls) == 1


def test_read_only_batches_still_parallelise():
    calls = [tc("read_page_text"), tc("observe"), tc("get_screen_context")]
    assert tool_worker_count(calls) == 3


def test_empty_batch_is_safe():
    assert tool_worker_count([]) == 1
