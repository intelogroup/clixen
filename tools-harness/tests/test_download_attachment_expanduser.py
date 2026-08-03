"""
Regression test for the 2026-07-11 download_attachment tilde-expansion bug:
Path(dest_dir) with no .expanduser() meant a caller-supplied "~/Desktop/x"
created a literal "./~/Desktop/x" relative to CWD instead of the real home
directory. Confirmed live via email.attachment_watch's config-driven dest_dir
(create_workflow's config used a real "~/Desktop/james_files" value).
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

from tools import gmail


def test_download_attachment_expands_tilde(tmp_path, monkeypatch):
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    fake_data = base64.urlsafe_b64encode(b"hello world").decode().rstrip("=")
    fake_service = MagicMock()
    fake_service.users.return_value.messages.return_value.attachments.return_value.get.return_value.execute.return_value = {
        "data": fake_data
    }

    with patch.object(gmail, "_gmail_service", return_value=fake_service), \
         patch.object(gmail, "execute_with_google_auth_retry", side_effect=lambda fn: fn()):
        result = gmail.download_attachment(
            message_id="msg1", attachment_id="att1",
            filename="test.txt", dest_dir="~/Desktop/james_files",
        )

    assert result, "download_attachment returned empty string"
    assert result.startswith(str(fake_home)), (
        f"expected path under fake HOME {fake_home}, got {result} — "
        "tilde was not expanded, a literal '~' directory was created instead"
    )
    assert not (tmp_path / "~").exists(), "literal '~' directory was created relative to CWD"
