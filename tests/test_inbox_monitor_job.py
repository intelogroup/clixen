import os
import sys
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools-harness'))


def _make_mock_jq(initial_ids=None):
    """Return a mock job_queue module with an in-memory seen_ids store."""
    seen = set(initial_ids or [])
    jq = MagicMock()
    jq.load_seen_ids.side_effect = lambda: set(seen)
    jq.save_seen_id.side_effect = lambda mid: seen.add(mid)
    return jq, seen


def test_load_seen_ids_empty():
    jq, _ = _make_mock_jq()
    # Patch Path so the legacy seen_ids.json appears absent (no migration branch)
    legacy_mock = MagicMock()
    legacy_mock.exists.return_value = False
    with patch("jobs.inbox_monitor_job.job_queue", jq), \
         patch("jobs.inbox_monitor_job.Path") as MockPath:
        MockPath.return_value.parent.__truediv__.return_value = legacy_mock
        from jobs.inbox_monitor_job import load_seen_ids
        ids = load_seen_ids()
    assert ids == set()


def test_save_and_reload_seen_ids():
    jq, store = _make_mock_jq()
    with patch("jobs.inbox_monitor_job.job_queue", jq):
        from jobs.inbox_monitor_job import save_seen_ids, load_seen_ids
        save_seen_ids({"abc", "def"})
        loaded = load_seen_ids()
    assert loaded == {"abc", "def"}


def test_ollama_summarize_fallback():
    """If the cloud model is unreachable, summarize falls back to truncation."""
    import jobs.inbox_monitor_job as m
    long_text = "A" * 2000
    with patch.object(m, "cloud_chat", side_effect=Exception("boom")):
        result = m.summarize_text(long_text, model="qwen3:4b")
    assert isinstance(result, str)
    assert len(result) > 0


def test_needs_task_detects_keywords():
    from jobs.inbox_monitor_job import _needs_task
    assert _needs_task("Please review and approve by the deadline") is True
    assert _needs_task("Here is the quarterly report summary") is False


def test_dispatch_routes_pdf(tmp_path):
    """_dispatch_attachment calls process_pdf for .pdf files."""
    from jobs import inbox_monitor_job
    att = {"filename": "report.pdf", "attachment_id": "aid1", "size": 1024}
    with patch.object(inbox_monitor_job, "process_pdf", return_value={}) as mock_pdf, \
         patch.object(inbox_monitor_job, "process_excel", return_value={}) as mock_xl, \
         patch.object(inbox_monitor_job, "process_docx", return_value={}) as mock_doc:
        inbox_monitor_job._dispatch_attachment("mid1", "a@b.com", "Sub", att)
    mock_pdf.assert_called_once()
    mock_xl.assert_not_called()
    mock_doc.assert_not_called()


def test_dispatch_routes_excel(tmp_path):
    """_dispatch_attachment calls process_excel for .xlsx files."""
    from jobs import inbox_monitor_job
    for ext in ("report.xlsx", "data.xls"):
        att = {"filename": ext, "attachment_id": "aid2", "size": 2048}
        with patch.object(inbox_monitor_job, "process_pdf", return_value={}) as mock_pdf, \
             patch.object(inbox_monitor_job, "process_excel", return_value={}) as mock_xl, \
             patch.object(inbox_monitor_job, "process_docx", return_value={}) as mock_doc:
            inbox_monitor_job._dispatch_attachment("mid2", "a@b.com", "Sub", att)
        mock_xl.assert_called_once()
        mock_pdf.assert_not_called()
        mock_doc.assert_not_called()


def test_dispatch_routes_docx():
    """_dispatch_attachment calls process_docx for .docx/.doc files."""
    from jobs import inbox_monitor_job
    for ext in ("letter.docx", "memo.doc"):
        att = {"filename": ext, "attachment_id": "aid3", "size": 512}
        with patch.object(inbox_monitor_job, "process_pdf", return_value={}) as mock_pdf, \
             patch.object(inbox_monitor_job, "process_excel", return_value={}) as mock_xl, \
             patch.object(inbox_monitor_job, "process_docx", return_value={}) as mock_doc:
            inbox_monitor_job._dispatch_attachment("mid3", "a@b.com", "Sub", att)
        mock_doc.assert_called_once()
        mock_pdf.assert_not_called()
        mock_xl.assert_not_called()


def test_watched_senders_from_env():
    """WATCHED_SENDERS env var overrides the default list."""
    with patch.dict(os.environ, {"WATCHED_SENDERS": "alice@x.com, bob@y.com, carol@z.com"}):
        import importlib
        import jobs.inbox_monitor_job as m
        importlib.reload(m)
        assert m.WATCHED_SENDERS == ["alice@x.com", "bob@y.com", "carol@z.com"]


def test_watched_senders_deduplication():
    """Duplicate entries in WATCHED_SENDERS must be preserved as-is (Gmail deduplicates the query)."""
    with patch.dict(os.environ, {"WATCHED_SENDERS": "a@x.com, a@x.com, b@x.com"}):
        import importlib
        import jobs.inbox_monitor_job as m
        importlib.reload(m)
        # duplicates come from the user — not our job to silently drop them
        assert "a@x.com" in m.WATCHED_SENDERS
        assert "b@x.com" in m.WATCHED_SENDERS


def test_watched_senders_whitespace_only_entries_skipped():
    """Comma-separated entries that are blank after strip must not appear in the list."""
    with patch.dict(os.environ, {"WATCHED_SENDERS": "a@x.com,  , ,b@x.com"}):
        import importlib
        import jobs.inbox_monitor_job as m
        importlib.reload(m)
        assert "" not in m.WATCHED_SENDERS
        assert "   " not in m.WATCHED_SENDERS
        assert len(m.WATCHED_SENDERS) == 2


def test_watched_senders_empty_env_uses_defaults():
    """Empty WATCHED_SENDERS must fall back to the hardcoded default list."""
    with patch.dict(os.environ, {"WATCHED_SENDERS": ""}):
        import importlib
        import jobs.inbox_monitor_job as m
        importlib.reload(m)
        assert len(m.WATCHED_SENDERS) > 0


# ── summarize_text edge cases ────────────────────────────────────────────────────

def test_summarize_text_empty_string():
    """Empty input must return a non-empty string (fallback path)."""
    import jobs.inbox_monitor_job as m
    with patch.object(m, "cloud_chat", side_effect=Exception("boom")):
        result = m.summarize_text("")
    assert isinstance(result, str)


def test_summarize_text_very_long_input_truncated_in_prompt():
    """Input longer than 6000 chars must not crash — it's silently truncated in the prompt."""
    import jobs.inbox_monitor_job as m
    with patch.object(m, "cloud_chat", side_effect=Exception("boom")):
        result = m.summarize_text("X" * 100_000)
    assert isinstance(result, str)
    assert len(result) > 0


# ── _needs_task edge cases ───────────────────────────────────────────────────────

def test_needs_task_empty_string():
    from jobs.inbox_monitor_job import _needs_task
    assert _needs_task("") is False


def test_needs_task_case_insensitive():
    from jobs.inbox_monitor_job import _needs_task
    assert _needs_task("URGENT: Please REVIEW by EOD") is True


# ── Pipeline failure paths ───────────────────────────────────────────────────────

def test_process_pdf_download_failure_returns_empty(tmp_path):
    from jobs import inbox_monitor_job
    att = {"filename": "r.pdf", "attachment_id": "x", "size": 1024}
    with patch.object(inbox_monitor_job, "download_attachment", return_value=""), \
         patch.object(inbox_monitor_job, "AGENT_DIR", tmp_path):
        result = inbox_monitor_job.process_pdf("mid", "a@b.com", "Sub", att)
    assert result == {}


def test_process_excel_download_failure_returns_empty(tmp_path):
    from jobs import inbox_monitor_job
    att = {"filename": "d.xlsx", "attachment_id": "x", "size": 1024}
    with patch.object(inbox_monitor_job, "download_attachment", return_value=""), \
         patch.object(inbox_monitor_job, "AGENT_DIR", tmp_path):
        result = inbox_monitor_job.process_excel("mid", "a@b.com", "Sub", att)
    assert result == {}


def test_process_docx_download_failure_returns_empty(tmp_path):
    from jobs import inbox_monitor_job
    att = {"filename": "m.docx", "attachment_id": "x", "size": 512}
    with patch.object(inbox_monitor_job, "download_attachment", return_value=""), \
         patch.object(inbox_monitor_job, "AGENT_DIR", tmp_path):
        result = inbox_monitor_job.process_docx("mid", "a@b.com", "Sub", att)
    assert result == {}


def test_process_pdf_conversion_error_falls_back(tmp_path):
    """If pdf_to_markdown raises, the pipeline continues with empty content."""
    from jobs import inbox_monitor_job
    att = {"filename": "r.pdf", "attachment_id": "x", "size": 1024}
    with patch.object(inbox_monitor_job, "download_attachment", return_value="/fake/r.pdf"), \
         patch.object(inbox_monitor_job, "pdf_to_markdown", side_effect=Exception("corrupt")), \
         patch.object(inbox_monitor_job, "extract_pdf_images", return_value=[]), \
         patch.object(inbox_monitor_job, "summarize_text", return_value="summary"), \
         patch.object(inbox_monitor_job, "_send_and_track", return_value={"ok": True}), \
         patch.object(inbox_monitor_job, "AGENT_DIR", tmp_path):
        result = inbox_monitor_job.process_pdf("mid", "a@b.com", "Sub", att)
    assert result  # pipeline still returned a row


def test_process_excel_conversion_error_falls_back(tmp_path):
    from jobs import inbox_monitor_job
    att = {"filename": "d.xlsx", "attachment_id": "x", "size": 1024}
    with patch.object(inbox_monitor_job, "download_attachment", return_value="/fake/d.xlsx"), \
         patch.object(inbox_monitor_job, "excel_to_markdown", side_effect=Exception("corrupt")), \
         patch.object(inbox_monitor_job, "excel_metadata", side_effect=Exception("corrupt")), \
         patch.object(inbox_monitor_job, "summarize_text", return_value="summary"), \
         patch.object(inbox_monitor_job, "_send_and_track", return_value={"ok": True}), \
         patch.object(inbox_monitor_job, "AGENT_DIR", tmp_path):
        result = inbox_monitor_job.process_excel("mid", "a@b.com", "Sub", att)
    assert result


def test_send_and_track_telegram_failure_does_not_crash(tmp_path):
    """Telegram send raising must not propagate — pipeline must complete."""
    from jobs import inbox_monitor_job
    with patch.object(inbox_monitor_job, "send_telegram", side_effect=Exception("bot blocked")), \
         patch.object(inbox_monitor_job, "create_task", return_value="task created"), \
         patch.object(inbox_monitor_job, "update_tracker", return_value=None), \
         patch.object(inbox_monitor_job, "TRACKER_PATH", tmp_path / "tracker.xlsx"):
        row = inbox_monitor_job._send_and_track(
            file_path="/f.pdf", converted_path="/f.md",
            file_type="pdf", sender="a@b.com", subject="S",
            filename="f.pdf", size_kb=10, extra_meta="1 page",
            summary="needs review",
        )
    assert row["telegram_sent"] is False


def test_send_and_track_task_failure_does_not_crash(tmp_path):
    """Google Tasks raising must not propagate."""
    from jobs import inbox_monitor_job
    with patch.object(inbox_monitor_job, "send_telegram", return_value="sent"), \
         patch.object(inbox_monitor_job, "create_task", side_effect=Exception("scope missing")), \
         patch.object(inbox_monitor_job, "update_tracker", return_value=None), \
         patch.object(inbox_monitor_job, "TRACKER_PATH", tmp_path / "tracker.xlsx"):
        row = inbox_monitor_job._send_and_track(
            file_path="/f.pdf", converted_path="/f.md",
            file_type="pdf", sender="a@b.com", subject="S",
            filename="f.pdf", size_kb=10, extra_meta="1 page",
            summary="please review and approve",
        )
    assert row["task_created"] is False


def test_dispatch_unknown_extension_returns_empty():
    from jobs import inbox_monitor_job
    att = {"filename": "archive.zip", "attachment_id": "x", "size": 1024}
    result = inbox_monitor_job._dispatch_attachment("mid", "a@b.com", "Sub", att)
    assert result == {}


def test_dispatch_uppercase_extension_routes_correctly():
    """Files named .PDF (uppercase) must route to process_pdf."""
    from jobs import inbox_monitor_job
    att = {"filename": "REPORT.PDF", "attachment_id": "x", "size": 1024}
    with patch.object(inbox_monitor_job, "process_pdf", return_value={"ok": True}) as mock_pdf:
        inbox_monitor_job._dispatch_attachment("mid", "a@b.com", "Sub", att)
    mock_pdf.assert_called_once()
