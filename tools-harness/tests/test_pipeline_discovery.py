"""
Regression test for list_available_pipelines / handler_registry.describe_all()
(2026-07-11) — the automation subagent had no way to discover which
automation_id values are legal for create_workflow (only already-deployed
instances via list_automations), confirmed live via a "watch sender, save
attachment, notify" request that couldn't find/reuse a suitable pipeline.
"""
from __future__ import annotations

from jobs import handler_registry
from jobs.worker import _setup_handler_registry


def test_describe_all_returns_full_wrapped_description():
    _setup_handler_registry()
    described = handler_registry.describe_all()
    assert "inbox.monitor_attachments" in described
    assert "email.attachment_watch" in described
    # Must not truncate at the first hard-wrapped source line.
    assert "configurable destination folder" in described["email.attachment_watch"]
    for automation_id, desc in described.items():
        assert not desc.lower().startswith("handler:"), automation_id


def test_list_available_pipelines_tool_lists_all_registered_ids():
    from tools.workflow_job_tools import list_available_pipelines

    _setup_handler_registry()
    result = list_available_pipelines({})
    assert "email.attachment_watch" in result
    assert "inbox.monitor_attachments" in result
    assert "user.automation" in result


def test_describe_all_surfaces_real_config_keys_not_just_summary():
    # 2026-07-11 follow-up: a one-line summary alone let the model guess a
    # plausible-but-wrong config key ("destination_folder" instead of the
    # handler's real "dest_dir") — verify the real key name is present.
    _setup_handler_registry()
    described = handler_registry.describe_all()
    assert "dest_dir" in described["email.attachment_watch"]
    assert "destination_folder" not in described["email.attachment_watch"]
