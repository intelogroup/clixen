"""
Tests for inbox_monitor_job — tick dispatch and error handling.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jobs import inbox_monitor_job
from jobs import job_queue


def make_email(**kw) -> dict:
    email = {
        "message_id": "msg_abc123",
        "sender": "sender@example.com",
        "subject": "Monthly Report",
        "date": "2026-07-18",
        "attachments": [{"filename": "report.pdf", "attachment_id": "att1", "size": 102400}],
    }
    email.update(kw)
    return email


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    db = tmp_path / "test_jobs.db"
    monkeypatch.setattr(job_queue, "DB_PATH", db)
    job_queue.init()

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    monkeypatch.setattr(inbox_monitor_job, "AGENT_DIR", agent_dir)
    monkeypatch.setattr(inbox_monitor_job, "TRACKER_PATH", tmp_path / "tracker.xlsx")

    # Always-mocked external services
    monkeypatch.setattr(inbox_monitor_job, "send_telegram", MagicMock(return_value="sent ok"))
    monkeypatch.setattr(inbox_monitor_job, "create_task", MagicMock(return_value="task created"))
    monkeypatch.setattr(inbox_monitor_job, "update_tracker", MagicMock())
    monkeypatch.setattr(inbox_monitor_job, "cloud_chat", MagicMock(return_value="- test summary"))
    monkeypatch.setattr(inbox_monitor_job, "notify", MagicMock())
    monkeypatch.setattr(inbox_monitor_job, "download_attachment", MagicMock(return_value=str(agent_dir / "test.pdf")))
    monkeypatch.setattr(inbox_monitor_job, "list_emails_with_attachments", MagicMock())

    # Stub fitz for lazy imports in process_pdf
    fitz_mock = MagicMock()
    doc_mock = MagicMock()
    doc_mock.needs_pass = False
    fitz_mock.open.return_value.__enter__.return_value = doc_mock
    monkeypatch.setitem(sys.modules, "fitz", fitz_mock)


class TestRunTick:

    def test_no_new_emails_returns_empty_set(self):
        inbox_monitor_job.list_emails_with_attachments.return_value = []
        seen = inbox_monitor_job.run_tick(set())
        assert seen == set()
        inbox_monitor_job.download_attachment.assert_not_called()

    def test_processes_pdf_attachment(self, monkeypatch):
        inbox_monitor_job.list_emails_with_attachments.return_value = [make_email()]
        monkeypatch.setattr(inbox_monitor_job, "pdf_to_markdown", MagicMock(return_value=("/tmp/conv.md", "markdown content")))
        monkeypatch.setattr(inbox_monitor_job, "extract_pdf_images", MagicMock(return_value=[]))

        seen = inbox_monitor_job.run_tick(set())

        assert "msg_abc123" in seen
        inbox_monitor_job.download_attachment.assert_called_once()
        inbox_monitor_job.pdf_to_markdown.assert_called_once()
        inbox_monitor_job.cloud_chat.assert_called_once()
        inbox_monitor_job.send_telegram.assert_called_once()
        inbox_monitor_job.update_tracker.assert_called_once()


class TestErrorHandling:

    def test_pdf_conversion_error_caught(self, monkeypatch):
        inbox_monitor_job.list_emails_with_attachments.return_value = [make_email()]
        monkeypatch.setattr(inbox_monitor_job, "pdf_to_markdown", MagicMock(side_effect=Exception("corrupt file")))
        monkeypatch.setattr(inbox_monitor_job, "extract_pdf_images", MagicMock(return_value=[]))

        seen = inbox_monitor_job.run_tick(set())

        assert "msg_abc123" in seen
        inbox_monitor_job.cloud_chat.assert_called_once()
        inbox_monitor_job.send_telegram.assert_called_once()

    def test_telegram_error_caught(self, monkeypatch):
        monkeypatch.setattr(inbox_monitor_job, "send_telegram", MagicMock(side_effect=Exception("network error")))
        inbox_monitor_job.list_emails_with_attachments.return_value = [make_email()]
        monkeypatch.setattr(inbox_monitor_job, "pdf_to_markdown", MagicMock(return_value=("/tmp/conv.md", "markdown content")))
        monkeypatch.setattr(inbox_monitor_job, "extract_pdf_images", MagicMock(return_value=[]))

        seen = inbox_monitor_job.run_tick(set())

        assert "msg_abc123" in seen
        inbox_monitor_job.update_tracker.assert_called_once()
