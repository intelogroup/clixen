"""
Edge case tests for tools/gmail.py attachment handling.
"""
import os
import sys
import base64
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools-harness'))
from unittest.mock import patch, MagicMock
from tools.gmail import list_emails_with_attachments, download_attachment, _find_attachments


# ── _find_attachments ───────────────────────────────────────────────────────────

def test_find_attachments_pdf(tmp_path):
    payload = {
        "filename": "report.pdf",
        "body": {"attachmentId": "att1", "size": 10000},
        "parts": [],
    }
    result = _find_attachments(payload)
    assert len(result) == 1
    assert result[0]["filename"] == "report.pdf"


def test_find_attachments_excel(tmp_path):
    for ext in ("data.xlsx", "data.xls"):
        payload = {
            "filename": ext,
            "body": {"attachmentId": "att2", "size": 5000},
            "parts": [],
        }
        result = _find_attachments(payload)
        assert len(result) == 1, f"expected match for {ext}"
        assert result[0]["filename"] == ext


def test_find_attachments_word(tmp_path):
    for ext in ("memo.docx", "memo.doc"):
        payload = {
            "filename": ext,
            "body": {"attachmentId": "att3", "size": 3000},
            "parts": [],
        }
        result = _find_attachments(payload)
        assert len(result) == 1, f"expected match for {ext}"


def test_find_attachments_uppercase_extension():
    """Extensions like .PDF and .XLSX must be matched case-insensitively."""
    for fname in ("REPORT.PDF", "DATA.XLSX", "MEMO.DOCX"):
        payload = {
            "filename": fname,
            "body": {"attachmentId": "attX", "size": 1000},
            "parts": [],
        }
        result = _find_attachments(payload)
        assert len(result) == 1, f"uppercase extension {fname} not matched"


def test_find_attachments_unsupported_extension():
    """ZIP, TXT, CSV and similar files must not be collected. Images now ARE
    collected (inline images carry real content — USPS Informed Delivery etc)."""
    for fname in ("archive.zip", "notes.txt", "data.csv", "script.exe"):
        payload = {
            "filename": fname,
            "body": {"attachmentId": "attY", "size": 500},
            "parts": [],
        }
        result = _find_attachments(payload)
        assert result == [], f"unsupported {fname} should not be returned"


def test_find_attachments_collects_inline_images():
    """Inline image parts (disp=inline, cid) are real email content and must be
    surfaced so the model can download + OCR them (USPS Informed Delivery)."""
    payload = {
        "filename": "1007699532-062.jpg",
        "body": {"attachmentId": "attImg", "size": 39641},
        "headers": [{"name": "Content-Disposition", "value": "inline; filename=\"1007699532-062.jpg\""}],
        "parts": [],
    }
    result = _find_attachments(payload)
    assert len(result) == 1
    assert result[0]["kind"] == "inline_image"
    assert result[0]["filename"] == "1007699532-062.jpg"


def test_find_attachments_no_attachment_id():
    """Inline parts without attachmentId must be ignored."""
    payload = {
        "filename": "report.pdf",
        "body": {"size": 1000},  # no attachmentId
        "parts": [],
    }
    assert _find_attachments(payload) == []


def test_find_attachments_nested_multipart():
    """Attachments buried in nested multipart/mixed → multipart/related must be found."""
    payload = {
        "filename": "",
        "body": {},
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "filename": "",
                "body": {},
                "mimeType": "multipart/related",
                "parts": [
                    {
                        "filename": "buried.pdf",
                        "body": {"attachmentId": "deep_att", "size": 8000},
                        "parts": [],
                    }
                ],
            }
        ],
    }
    result = _find_attachments(payload)
    assert len(result) == 1
    assert result[0]["filename"] == "buried.pdf"


def test_find_attachments_zero_size():
    """Zero-byte attachments must still be collected (download will confirm validity)."""
    payload = {
        "filename": "empty.pdf",
        "body": {"attachmentId": "att_zero", "size": 0},
        "parts": [],
    }
    result = _find_attachments(payload)
    assert len(result) == 1
    assert result[0]["size"] == 0


def test_find_attachments_mixed_types_in_one_email():
    """An email with a PDF, an xlsx, and a docx all in one payload."""
    payload = {
        "filename": "",
        "body": {},
        "parts": [
            {"filename": "report.pdf", "body": {"attachmentId": "a1", "size": 100}, "parts": []},
            {"filename": "data.xlsx",  "body": {"attachmentId": "a2", "size": 200}, "parts": []},
            {"filename": "memo.docx",  "body": {"attachmentId": "a3", "size": 300}, "parts": []},
            {"filename": "junk.zip",   "body": {"attachmentId": "a4", "size": 400}, "parts": []},
        ],
    }
    result = _find_attachments(payload)
    assert len(result) == 3
    assert {r["filename"] for r in result} == {"report.pdf", "data.xlsx", "memo.docx"}


# ── list_emails_with_attachments ────────────────────────────────────────────────

def _mock_svc_with_message(msg_id, filename, att_id="att_001", size=50000, sender="test@example.com"):
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    mime_map = {
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    svc = MagicMock()
    svc.users().messages().list().execute.return_value = {"messages": [{"id": msg_id}]}
    svc.users().messages().get().execute.return_value = {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": "Test attachment"},
                {"name": "Date", "value": "Thu, 10 Apr 2026 10:00:00 +0000"},
            ],
            "parts": [
                {
                    "mimeType": mime_map.get(ext, "application/octet-stream"),
                    "filename": filename,
                    "body": {"attachmentId": att_id, "size": size},
                    "parts": [],
                }
            ],
        },
    }
    return svc


def test_list_emails_finds_pdf():
    svc = _mock_svc_with_message("msg1", "report.pdf")
    with patch("tools.gmail._gmail_service", return_value=svc):
        results = list_emails_with_attachments(senders=["test@example.com"], seen_ids=set())
    assert len(results) == 1
    assert results[0]["attachments"][0]["filename"] == "report.pdf"


def test_list_emails_finds_xlsx():
    svc = _mock_svc_with_message("msg2", "data.xlsx")
    with patch("tools.gmail._gmail_service", return_value=svc):
        results = list_emails_with_attachments(senders=["test@example.com"], seen_ids=set())
    assert len(results) == 1
    assert results[0]["attachments"][0]["filename"] == "data.xlsx"


def test_list_emails_finds_docx():
    svc = _mock_svc_with_message("msg3", "memo.docx")
    with patch("tools.gmail._gmail_service", return_value=svc):
        results = list_emails_with_attachments(senders=["test@example.com"], seen_ids=set())
    assert len(results) == 1
    assert results[0]["attachments"][0]["filename"] == "memo.docx"


def test_list_emails_skips_seen_id():
    svc = _mock_svc_with_message("msg4", "old.pdf")
    with patch("tools.gmail._gmail_service", return_value=svc):
        results = list_emails_with_attachments(senders=["test@example.com"], seen_ids={"msg4"})
    assert results == []


def test_list_emails_no_service_returns_empty():
    with patch("tools.gmail._gmail_service", return_value=None):
        results = list_emails_with_attachments(senders=["test@example.com"], seen_ids=set())
    assert results == []


def test_list_emails_api_exception_returns_empty():
    svc = MagicMock()
    svc.users().messages().list().execute.side_effect = Exception("network error")
    with patch("tools.gmail._gmail_service", return_value=svc):
        results = list_emails_with_attachments(senders=["test@example.com"], seen_ids=set())
    assert results == []


def test_list_emails_display_name_sender():
    """'Boss Name <boss@company.com>' in From header must be stored as-is."""
    svc = _mock_svc_with_message("msg5", "doc.pdf", sender='"Boss Name" <boss@company.com>')
    with patch("tools.gmail._gmail_service", return_value=svc):
        results = list_emails_with_attachments(senders=["boss@company.com"], seen_ids=set())
    assert len(results) == 1
    assert "boss@company.com" in results[0]["sender"]


# ── download_attachment ─────────────────────────────────────────────────────────

def test_download_attachment_saves_file(tmp_path):
    svc = MagicMock()
    fake_bytes = b"%PDF-1.4 fake content"
    encoded = base64.urlsafe_b64encode(fake_bytes).decode()
    svc.users().messages().attachments().get().execute.return_value = {"data": encoded}
    with patch("tools.gmail._gmail_service", return_value=svc):
        dest = download_attachment("msg1", "att1", "doc.pdf", str(tmp_path))
    assert dest.endswith("doc.pdf")
    assert (tmp_path / "doc.pdf").read_bytes() == fake_bytes


def test_download_attachment_no_service_returns_empty(tmp_path):
    with patch("tools.gmail._gmail_service", return_value=None):
        result = download_attachment("msg1", "att1", "doc.pdf", str(tmp_path))
    assert result == ""


def test_download_attachment_api_exception_returns_empty(tmp_path):
    svc = MagicMock()
    svc.users().messages().attachments().get().execute.side_effect = Exception("timeout")
    with patch("tools.gmail._gmail_service", return_value=svc):
        result = download_attachment("msg1", "att1", "doc.pdf", str(tmp_path))
    assert result == ""
