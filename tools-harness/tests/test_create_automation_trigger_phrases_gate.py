"""
Regression for the live prompt-only-rule failure (2026-07-17): a trigger-test
of the new "ask before building automations with unstated defaults" prompt
rule showed the orchestrator built a watch_contact iMessage auto-responder
with zero filtering and never asked what actually counts as a real request
vs. noise (see ORCHESTRATOR_SYSTEM_PROMPT's create_automation bullet +
skills_data/automation.py's Automation Edge Case Checklist skill). This test
pins the code-level backstop: create_automation() itself must refuse to
create a watch_contact automation with no `trigger_phrases`, rather than
relying on the LLM to remember to ask.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import store.workflow_store as wf_mod
from tools import automation_tools as at


def test_watch_contact_without_trigger_phrases_is_rejected(monkeypatch):
    created = []
    monkeypatch.setattr(wf_mod, "create_workflow_instance",
                         lambda **kw: created.append(kw) or {"id": "should-not-happen"})

    result = at.create_automation({
        "name": "Job Offer Auto-Responder",
        "trigger_type": "schedule",
        "action_type": "imessage",
        "schedule": {"cron": "* 7-22 * * *", "timezone": "America/New_York"},
        "action_config": {
            "watch_contact": "+14155551234",
            "reply_message": "Yes I can!",
        },
    })

    assert "trigger_phrases" in result
    assert "needs clarification" in result.lower()
    assert created == []  # must not have created anything


def test_watch_contact_with_trigger_phrases_proceeds(monkeypatch):
    created = []
    monkeypatch.setattr(wf_mod, "create_workflow_instance",
                         lambda **kw: created.append(kw) or {"id": "wf-123"})

    result = at.create_automation({
        "name": "Job Offer Auto-Responder",
        "trigger_type": "schedule",
        "action_type": "imessage",
        "schedule": {"cron": "* 7-22 * * *", "timezone": "America/New_York"},
        "action_config": {
            "watch_contact": "+14155551234",
            "reply_message": "Yes I can!",
            "trigger_phrases": ["are you available", "can you cover"],
        },
    })

    assert "needs clarification" not in result.lower()
    assert len(created) == 1
    assert created[0]["config"]["trigger_phrases"] == ["are you available", "can you cover"]


def test_plain_send_imessage_not_gated(monkeypatch):
    # A plain one-off send (no watch_contact) has no ambiguity to ask
    # about — the gate must not fire for it.
    created = []
    monkeypatch.setattr(wf_mod, "create_workflow_instance",
                         lambda **kw: created.append(kw) or {"id": "wf-456"})

    result = at.create_automation({
        "name": "One-off ping",
        "trigger_type": "schedule",
        "action_type": "imessage",
        "schedule": {"cron": "0 9 * * *", "timezone": "America/New_York"},
        "action_config": {"recipient": "+14155551234", "message": "hi"},
    })

    assert "needs clarification" not in result.lower()
    assert len(created) == 1
