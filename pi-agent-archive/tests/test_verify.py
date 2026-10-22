"""verify.py — LLM gate behavior (mocked OpenRouter; no real API in tests)."""
import os
from pathlib import Path
from unittest import mock

from base import TrackerTestCase
import verify as vf


GOOD = """# Daily Briefing — 2026-09-02

## AI
**1. Model X released** (https://x.com/1) Short summary here.

## Science
**1. Fusion result** (https://s.com/1) Summary.

## Tech
**1. Chip launch** (https://t.com/1) Summary.
"""


def _resp(content: str, status: int = 200):
    m = mock.Mock()
    m.status_code = status
    m.json.return_value = {"choices": [{"message": {"content": content}}]}
    return m


class TestVerify(TrackerTestCase):
    def _env(self):
        return {"OPENROUTER_API_KEY": "k", "PI_MODEL": "openrouter/test/model"}

    def test_missing_file_fails(self):
        ok, reason = vf.verify_briefing("/nonexistent.md")
        assert not ok

    def test_too_short_fails(self):
        p = Path(self.tmp) / "b.md"
        p.write_text("# short")
        ok, reason = vf.verify_briefing(str(p))
        assert not ok
        assert "too short" in reason

    def test_pass_verdict(self):
        p = Path(self.tmp) / "b.md"
        p.write_text(GOOD)
        with mock.patch.dict(os.environ, self._env(), clear=True):
            with mock.patch("requests.post", return_value=_resp("VERDICT: PASS\nNotes.")):
                ok, reason = vf.verify_briefing(str(p))
        assert ok
        assert reason == "Notes."

    def test_fail_verdict(self):
        p = Path(self.tmp) / "b.md"
        p.write_text(GOOD)
        with mock.patch.dict(os.environ, self._env(), clear=True):
            with mock.patch("requests.post",
                            return_value=_resp("VERDICT: FAIL: same story twice")):
                ok, reason = vf.verify_briefing(str(p))
        assert not ok
        assert "same story twice" in reason

    def test_contract_violation_fails(self):
        p = Path(self.tmp) / "b.md"
        p.write_text(GOOD)
        with mock.patch.dict(os.environ, self._env(), clear=True):
            with mock.patch("requests.post",
                            return_value=_resp("Here is my review... looks good!")):
                ok, reason = vf.verify_briefing(str(p))
        assert not ok
        assert "contract" in reason

    def test_api_error_retries_then_fails_open(self):
        # 2 attempts both raise -> fail-open with note (transient API outage
        # must not silently drop briefings)
        p = Path(self.tmp) / "b.md"
        p.write_text(GOOD)
        with mock.patch.dict(os.environ, self._env(), clear=True):
            with mock.patch("requests.post", side_effect=RuntimeError("boom")):
                with mock.patch("time.sleep"):
                    ok, reason = vf.verify_briefing(str(p))
        assert ok
        assert "fail-open" in reason

    def test_api_500_fails_open(self):
        p = Path(self.tmp) / "b.md"
        p.write_text(GOOD)
        with mock.patch.dict(os.environ, self._env(), clear=True):
            with mock.patch("requests.post", return_value=_resp("err", status=500)):
                with mock.patch("time.sleep"):
                    ok, reason = vf.verify_briefing(str(p))
        assert ok
        assert "fail-open" in reason
