"""
Regression test: Gmail attachment filenames are attacker-controlled (anyone
can email a file named "../../.ssh/authorized_keys" or an absolute path).
download_attachment() used to join dest_dir/filename with no sanitization —
Path.__truediv__ silently drops dest_dir entirely when filename is absolute,
and ".." segments escape dest_dir when it isn't. Fixed by taking Path(filename).name.
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

from tools import gmail


def _download(tmp_path, filename):
    dest_dir = tmp_path / "dest"
    fake_data = base64.urlsafe_b64encode(b"payload").decode().rstrip("=")
    fake_service = MagicMock()
    fake_service.users.return_value.messages.return_value.attachments.return_value.get.return_value.execute.return_value = {
        "data": fake_data
    }
    with patch.object(gmail, "_gmail_service", return_value=fake_service), \
         patch.object(gmail, "execute_with_google_auth_retry", side_effect=lambda fn: fn()):
        return gmail.download_attachment(
            message_id="msg1", attachment_id="att1",
            filename=filename, dest_dir=str(dest_dir),
        )


def test_download_attachment_blocks_relative_traversal(tmp_path):
    result = _download(tmp_path, "../../../etc/evil_cron")
    assert result, "download_attachment returned empty string"
    assert str(tmp_path / "dest") in result
    assert "etc" not in result.split(str(tmp_path))[-1].replace("dest", "")


def test_download_attachment_blocks_absolute_path(tmp_path):
    outside = tmp_path / "outside_marker"
    result = _download(tmp_path, str(outside))
    assert result, "download_attachment returned empty string"
    assert not outside.exists(), "absolute filename escaped dest_dir"
    assert str(tmp_path / "dest") in result
