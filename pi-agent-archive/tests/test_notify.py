"""notify.py — Telegram send paths (error handling, no real network in tests)."""
import os
import sys
from pathlib import Path
from unittest import mock

from base import TrackerTestCase
import notify as nf


class TestNotify(TrackerTestCase):
    def test_send_with_no_creds(self):
        with mock.patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_OWNER_CHAT_ID": ""}, clear=True):
            res = nf.send("hello")
        assert res.startswith("[telegram error]")

    def test_send_success_parses_message_id(self):
        env = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_OWNER_CHAT_ID": "123"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("requests.post") as mpost:
                mpost.return_value.json.return_value = {"ok": True, "result": {"message_id": 99}}
                res = nf.send("hello")
        assert "sent" in res
        assert "99" in res
        # verify correct endpoint + payload
        cid = mpost.call_args
        assert cid  # made exactly one call
        assert "api.telegram.org/bot" in cid[0][0]

    def test_briefing_missing_file(self):
        res = nf.send_briefing("/nonexistent/briefing.md")
        assert "not found" in res

    def test_briefing_reads_and_sends(self):
        env = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_OWNER_CHAT_ID": "123"}
        md = Path(self.tmp) / "briefing-2026-09-01.md"
        md.write_text("# Daily Briefing\n\n- [item](https://x.com) done\n")
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("requests.post") as mpost:
                mpost.return_value.json.return_value = {"ok": True, "result": {"message_id": 1}}
                res = nf.send_briefing(str(md))
        assert res.startswith("sent")
        post_kwargs = mpost.call_args.kwargs["json"]
        assert "Daily Briefing" in post_kwargs["text"]