"""
Tests for scripts/daily_email_summary.py — helper functions and pipeline resilience.
All external calls (Gmail, Ollama, Telegram) are mocked.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

import scripts.daily_email_summary as m


# ── Helper functions ────────────────────────────────────────────────────────────

def test_extract_ids_empty():
    assert m._extract_ids("") == []


def test_extract_ids_no_ids():
    assert m._extract_ids("No emails found.\nSome other line") == []


def test_extract_ids_single():
    listing = "Subject: Hello\nID: abc123\nFrom: someone"
    assert m._extract_ids(listing) == ["abc123"]


def test_extract_ids_multiple():
    listing = "ID: aaa\nID: bbb\nID: ccc"
    assert m._extract_ids(listing) == ["aaa", "bbb", "ccc"]


def test_extract_ids_strips_whitespace():
    listing = "  ID:   xyz789  "
    assert m._extract_ids(listing) == ["xyz789"]


def test_chunk_text_fits_in_one():
    text = "hello"
    assert m._chunk_text(text, size=100) == ["hello"]


def test_chunk_text_splits_evenly():
    text = "a" * 10
    chunks = m._chunk_text(text, size=3)
    assert chunks == ["aaa", "aaa", "aaa", "a"]


def test_chunk_text_empty():
    assert m._chunk_text("") == []


def test_strip_asterisks_removes_all():
    assert m._strip_asterisks("**bold** and *italic*") == "bold and italic"


def test_strip_asterisks_no_asterisks():
    assert m._strip_asterisks("plain text") == "plain text"


def test_stage1_prompt_contains_email():
    email = "From: alice@x.com\nSubject: Test"
    prompt = m._stage1_prompt(email)
    assert email in prompt
    assert "Summarize" in prompt
    assert "action" in prompt.lower()


def test_stage2_prompt_contains_all_summaries():
    summaries = ["Summary A", "Summary B"]
    prompt = m._stage2_prompt(summaries)
    assert "Summary A" in prompt
    assert "Summary B" in prompt
    assert "Overall summary" in prompt
    assert "Action items" in prompt
    assert "dates" in prompt.lower()


def test_self_test_data_returns_two_emails():
    data = m._self_test_data()
    assert len(data) == 2
    assert all("From:" in d for d in data)


# ── main() pipeline ─────────────────────────────────────────────────────────────

def _env(token="tok", chat_id="123"):
    return {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_OWNER_CHAT_ID": chat_id}


def test_main_missing_token_returns_2(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_OWNER_CHAT_ID", raising=False)
    with patch.object(m, "_load_env"):  # prevent .env reload from repopulating vars
        rc = m.main()
    assert rc == 2


def test_main_no_emails_sends_empty_notice(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    with patch.object(m, "list_emails", return_value="No emails found.") as mock_list, \
         patch.object(m, "_send_telegram") as mock_tg:
        rc = m.main()
    assert rc == 0
    mock_tg.assert_called_once()
    assert "no emails" in mock_tg.call_args[0][2].lower()


def test_main_no_parsable_ids(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    with patch.object(m, "list_emails", return_value="Subject: Something\nNo IDs here"), \
         patch.object(m, "_send_telegram") as mock_tg:
        rc = m.main()
    assert rc == 0
    mock_tg.assert_called_once()
    assert "no emails" in mock_tg.call_args[0][2].lower()


def test_main_full_pipeline(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    listing = "ID: msg001\nID: msg002"
    email_body = "From: alice@x.com\nSubject: Report\n\nHi, please review."
    with patch.object(m, "list_emails", return_value=listing), \
         patch.object(m, "read_email", return_value=email_body), \
         patch.object(m, "ollama_chat", return_value="- Summary bullet") as mock_llm, \
         patch.object(m, "_send_telegram") as mock_tg:
        rc = m.main()
    assert rc == 0
    assert mock_llm.call_count >= 2  # stage1 per email + stage2
    mock_tg.assert_called_once()
    sent_text = mock_tg.call_args[0][2]
    assert "Daily email summary" in sent_text


def test_main_dry_run_prints_and_does_not_telegram(monkeypatch, capsys):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("EMAIL_SUMMARY_DRY_RUN", "1")
    listing = "ID: msg001"
    email_body = "From: bob@x.com\nSubject: Hello"
    with patch.object(m, "list_emails", return_value=listing), \
         patch.object(m, "read_email", return_value=email_body), \
         patch.object(m, "ollama_chat", return_value="- Bullet"), \
         patch.object(m, "_send_telegram") as mock_tg:
        rc = m.main()
    assert rc == 0
    mock_tg.assert_not_called()
    captured = capsys.readouterr()
    assert "Daily email summary" in captured.out


def test_main_self_test_mode(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("EMAIL_SUMMARY_SELF_TEST", "1")
    with patch.object(m, "ollama_chat", return_value="- Item") as mock_llm, \
         patch.object(m, "_send_telegram") as mock_tg:
        rc = m.main()
    assert rc == 0
    # 2 self-test emails → 2 stage1 calls + 1 stage2 call
    assert mock_llm.call_count == 3
    mock_tg.assert_called_once()


def test_main_read_email_error_skips_body(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    listing = "ID: bad001\nID: bad002"
    with patch.object(m, "list_emails", return_value=listing), \
         patch.object(m, "read_email", return_value="[gmail error] auth failed"), \
         patch.object(m, "_send_telegram") as mock_tg:
        rc = m.main()
    assert rc == 0
    # All bodies skipped → "no readable emails" message
    mock_tg.assert_called_once()
    assert "no readable" in mock_tg.call_args[0][2].lower()



