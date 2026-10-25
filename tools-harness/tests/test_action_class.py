"""The action-class contract: what a tool call *does*, declared, never inferred.

Written after the cloud repo's `capabilities/adapter/spec.py` ("the action class
decides approval behaviour") and ADR 0011 ("a spec that fails validation means the
tool does not exist"). The local point is narrower but concrete: the orb's rule 6 —
irreversible actions need the user to have asked for exactly that — was prompt
prose, so it held only as far as the model followed it.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import action_class as ac  # noqa: E402
from tools.registry import ALL_TOOLS  # noqa: E402


@pytest.mark.parametrize(
    "name,expected",
    [
        ("observe", ac.READ),
        ("read_page_text", ac.READ),
        ("system_status", ac.READ),
        ("act", ac.WRITE),
        ("workspace_write", ac.WRITE),
        ("workspace_read", ac.READ),
        ("bash_exec", ac.WRITE),
        ("send_email", ac.MONEY),
    ],
)
def test_declared_classes(name, expected):
    assert ac.of(name) == expected


def test_app_task_is_judged_by_its_recipe():
    assert ac.of("app_task") == ac.WRITE
    assert ac.of_call("app_task", {"name": "gmail.send"}) == ac.WRITE
    assert ac.of_call("app_task", {"name": "web.read"}) == ac.WRITE  # base class, still a write


def test_unknown_writers_default_to_write_not_read():
    # Fail safe: an unclassified verb that looks like it changes state is a write.
    assert ac.of("send_slack_message") == ac.WRITE
    assert ac.of("delete_file") == ac.WRITE
    assert ac.of("transfer_funds") == ac.MONEY


def test_reads_do_not_need_approval_and_money_always_does(monkeypatch):
    monkeypatch.delenv("CLIXEN_APPROVALS", raising=False)
    assert ac.requires_approval("observe") is False
    assert ac.requires_approval("act") is False          # writes are not parked by default
    assert ac.requires_approval("send_email") is True     # money always is


def test_write_approvals_switch(monkeypatch):
    monkeypatch.setenv("CLIXEN_APPROVALS", "1")
    assert ac.requires_approval("act") is True
    assert ac.requires_approval("observe") is False


def test_every_orb_tool_has_a_class():
    import orb_agent

    for name in orb_agent.TOOL_NAMES:
        assert ac.of(name) in (ac.READ, ac.WRITE, ac.MONEY), name


def test_registry_covers_every_schema_without_blowing_up():
    names = [t["function"]["name"] for t in ALL_TOOLS]
    line = ac.summary(names)
    assert "read" in line and "write" in line
    # sanity: a registry of 350 mixed tools must not classify as all-read
    reads = sum(1 for n in names if ac.of(n) == ac.READ)
    assert 50 < reads < len(names)
