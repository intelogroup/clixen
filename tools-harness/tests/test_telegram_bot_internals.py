"""
Telegram bot internals beyond the doc-agent dispatch and grounding-policy surfaces:
retry/backoff, long-message chunking, screenshot continuation/reference guards,
screenshot-cache sweep, inflight marking, markdown stripping, photo-send intent,
allowed-user gate, stale-message age.
Pure/mocked — no network, no Telegram API calls.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from telegram.error import NetworkError, TimedOut

import telegram_bot as tb
from tools import inflight_tracker as _inflight

# ---------------------------------------------------------------------------
# _tg_retry — backoff + final re-raise
# ---------------------------------------------------------------------------


class _Sleeper:
    def __init__(self):
        self.calls = []

    async def sleep(self, secs):
        self.calls.append(secs)


@pytest.mark.asyncio
async def test_tg_retry_succeeds_after_transient_failures_with_backoff(monkeypatch):
    sleeper = _Sleeper()
    monkeypatch.setattr(tb.asyncio, "sleep", sleeper.sleep)
    attempts = []
    calls = {"n": 0}

    async def flaky():
        attempts.append(calls["n"])
        calls["n"] += 1
        if calls["n"] <= 3:
            raise NetworkError("boom")
        return "ok"

    result = await tb._tg_retry(flaky, label="test", attempts=4)
    assert result == "ok"
    assert len(attempts) == 4
    assert sleeper.calls == [1.5, 3.0, 6.0]  # 1.5 * 2**i


@pytest.mark.asyncio
async def test_tg_retry_reraises_on_final_failure(monkeypatch):
    sleeper = _Sleeper()
    monkeypatch.setattr(tb.asyncio, "sleep", sleeper.sleep)

    async def always_fail():
        raise TimedOut("dead")

    with pytest.raises(TimedOut):
        await tb._tg_retry(always_fail, label="test", attempts=2)
    assert sleeper.calls == [1.5]  # only slept once before final raise


@pytest.mark.asyncio
async def test_tg_retry_does_not_swallow_non_network_errors():
    async def boom():
        raise ValueError("not transient")

    with pytest.raises(ValueError):
        await tb._tg_retry(boom, label="test", attempts=3)


# ---------------------------------------------------------------------------
# _send_long — 4096-char chunking + per-chunk retry
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, reply_handler=None):
        self.replied = []
        self._reply_handler = reply_handler

    async def reply_text(self, text):
        if self._reply_handler is not None:
            return await self._reply_handler(text)
        self.replied.append(text)


class _FakeUpdate:
    def __init__(self, message):
        self.message = message


@pytest.mark.asyncio
async def test_send_long_chunks_at_4096():
    msg = _FakeMessage()
    await tb._send_long(_FakeUpdate(msg), "x" * 9000)
    assert [len(c) for c in msg.replied] == [4096, 4096, 808]
    assert "".join(msg.replied) == "x" * 9000


@pytest.mark.asyncio
async def test_send_long_strips_markdown_before_chunking():
    msg = _FakeMessage()
    await tb._send_long(_FakeUpdate(msg), "**bold** *it* `code` ~strike~ # H1")
    # single-tilde ~strike~ is NOT matched by the regex (only ~~ is)
    assert "".join(msg.replied) == "bold it code ~strike~ H1"


@pytest.mark.asyncio
async def test_send_long_retries_chunk_on_network_error_then_gives_up(monkeypatch):
    sleeper = _Sleeper()
    monkeypatch.setattr(tb.asyncio, "sleep", sleeper.sleep)
    state = {"fail_first": 1, "replied": []}

    async def flaky_reply(text):
        if state["fail_first"] > 0:
            state["fail_first"] -= 1
            raise NetworkError("blip")
        state["replied"].append(text)

    await tb._send_long(_FakeUpdate(_FakeMessage(reply_handler=flaky_reply)), "z" * 5000)

    # first chunk retried (1 failure) then succeeded; second chunk clean
    assert state["fail_first"] == 0
    assert "".join(state["replied"]) == "z" * 5000
    assert len(sleeper.calls) == 1  # one backoff for the single transient blip


@pytest.mark.asyncio
async def test_send_long_raises_after_three_failures_on_same_chunk(monkeypatch):
    sleeper = _Sleeper()
    monkeypatch.setattr(tb.asyncio, "sleep", sleeper.sleep)

    async def always_fail(text):
        raise NetworkError("down")

    with pytest.raises(NetworkError):
        await tb._send_long(_FakeUpdate(_FakeMessage(reply_handler=always_fail)), "a" * 5000)
    assert len(sleeper.calls) == 2  # slept between the 3 attempts


# ---------------------------------------------------------------------------
# _strip_md
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,clean", [
    ("**bold**", "bold"),
    ("_italic_", "italic"),
    ("`inline`", "inline"),
    ("~~gone~~", "gone"),
    ("### header", "header"),
    ("**bold** and `code`", "bold and code"),
    ("no markdown", "no markdown"),
])
def test_strip_md(raw, clean):
    assert tb._strip_md(raw) == clean


# ---------------------------------------------------------------------------
# _is_screenshot_request / reference vs capture near-guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("q", [
    "take a screenshot",
    "capture the screen shot",
    "show me my screen",
    "grab another screenshot",
    "send me a screenshot",
])
def test_screenshot_capture_request(q):
    assert tb._is_screenshot_request(q) is True


@pytest.mark.parametrize("q", [
    "use data from the last screenshot",
    "in that screenshot what is the total",
    "go back to the first screenshot",
    "save this in excel and send it to me",  # "send" far from "screenshot"
])
def test_screenshot_reference_is_not_new_capture(q):
    assert tb._is_screenshot_request(q) is False


@pytest.mark.parametrize("q", [
    "send the screenshot to bob",  # capture verb governs screenshot
    "show the screenshot again",
])
def test_capture_verb_near_screenshot_wins_over_reference(q):
    assert tb._is_screenshot_request(q) is True


# ---------------------------------------------------------------------------
# _is_screenshot_continuation — routes vague follow-ups to the capture path
# ---------------------------------------------------------------------------


def _conv_turns(*turns):
    return [{"role": r, "content": c} for r, c in turns]


def test_screenshot_continuation_fires_when_last_assistant_has_ocr_marker(monkeypatch):
    import store.conversation as conv
    history = _conv_turns(
        ("user", "what's on my screen"),
        ("assistant", f"Here's the readout:\n{tb._SCREENSHOT_OCR_MARKER} ... ]\nAnswer: 42"),
    )
    monkeypatch.setattr(conv, "get", lambda chat_id: history)
    assert tb._is_screenshot_continuation("keep going", "chat1") is True


def test_screenshot_continuation_ignores_plain_then_no_marker(monkeypatch):
    import store.conversation as conv
    history = _conv_turns(
        ("user", "hi"),
        ("assistant", "hello!"),
    )
    monkeypatch.setattr(conv, "get", lambda chat_id: history)
    assert tb._is_screenshot_continuation("keep going", "chat1") is False


@pytest.mark.parametrize("q", [
    "keep going",
    "same as before",
    "do it again",
    "next question",
    "continue",
    "one more",
])
def test_continuation_phrases_match(q):
    assert tb._SCREENSHOT_CONTINUATION_RE.search(q)


def test_continuation_only_checks_latest_assistant_turn(monkeypatch):
    import store.conversation as conv
    # older assistant had marker, latest one does not → not a continuation
    history = _conv_turns(
        ("user", "x"),
        ("assistant", f"{tb._SCREENSHOT_OCR_MARKER} ]"),
        ("user", "keep going"),
        ("assistant", "plain answer"),
    )
    monkeypatch.setattr(conv, "get", lambda chat_id: history)
    assert tb._is_screenshot_continuation("keep going", "chat1") is False


# ---------------------------------------------------------------------------
# _sweep_screenshot_cache
# ---------------------------------------------------------------------------


def test_sweep_removes_stale_keeps_fresh(monkeypatch, tmp_path):
    import os as _os
    import time as _time

    stale = tmp_path / "stale.jpg"
    fresh = tmp_path / "fresh.jpg"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"y")

    old = _time.time() - 7200  # 2h > 1h TTL
    _os.utime(stale, (old, old))
    monkeypatch.setattr(tb, "_SCREENSHOT_CACHE_DIR", tmp_path)

    tb._sweep_screenshot_cache()
    assert not stale.exists()
    assert fresh.exists()


def test_sweep_tolerates_unreadable_file(monkeypatch, tmp_path):
    import os as _os
    import time as _time

    old = _time.time() - 7200
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"x")
    _os.utime(bad, (old, old))
    good = tmp_path / "good.jpg"
    good.write_bytes(b"y")

    # make stat() raise OSError for the stale file only — sweep must skip, not crash
    orig_stat = tb.Path.stat

    def _stat(self, **kwargs):
        if str(self) == str(bad):
            raise OSError("nope")
        return orig_stat(self, **kwargs)

    monkeypatch.setattr(tb.Path, "stat", _stat)
    monkeypatch.setattr(tb, "_SCREENSHOT_CACHE_DIR", tmp_path)
    tb._sweep_screenshot_cache()
    remaining = {p.name for p in tmp_path.iterdir()}
    assert "bad.jpg" in remaining  # couldn't stat → left alone
    assert "good.jpg" in remaining  # fresh → kept


# ---------------------------------------------------------------------------
# inflight marking (channel-scoped)
# ---------------------------------------------------------------------------


def test_mark_and_clear_inflight(monkeypatch, tmp_path):
    monkeypatch.setattr(_inflight, "_DIR", tmp_path)
    tb._mark_inflight("chat9", "summarize")
    data = json.loads(_inflight._file("telegram").read_text())
    assert data == {"chat_id": "chat9", "query": "summarize"}
    tb._clear_inflight()
    assert not _inflight._file("telegram").exists()


# ---------------------------------------------------------------------------
# _wants_photo_sent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("q,expected", [
    ("send it to me", True),
    ("show me the screen", True),
    ("show the screenshot", True),
    ("just read it out loud", False),
    ("what is the answer on my screen", False),  # analyze-only, no send/show verb
])
def test_wants_photo_sent(q, expected):
    assert tb._wants_photo_sent(q) is expected


# ---------------------------------------------------------------------------
# _allowed
# ---------------------------------------------------------------------------


def test_allowed_empty_allowlist_allows_everyone(monkeypatch):
    monkeypatch.setattr(tb, "ALLOWED_USER_IDS", set())
    assert tb._allowed(_FakeUpdate(_FakeMessage())) is True


def test_allowed_restricts_to_allowlist(monkeypatch):
    monkeypatch.setattr(tb, "ALLOWED_USER_IDS", {123})
    allowed = SimpleNamespace(effective_user=SimpleNamespace(id=123))
    denied = SimpleNamespace(effective_user=SimpleNamespace(id=999))
    assert tb._allowed(allowed) is True
    assert tb._allowed(denied) is False


# ---------------------------------------------------------------------------
# _msg_age_secs
# ---------------------------------------------------------------------------

import datetime


def test_msg_age_secs_fresh_and_stale(monkeypatch):
    import telegram_bot as _tb
    now = datetime.datetime.now(datetime.timezone.utc)

    fresh = _FakeUpdate(_FakeMessage())
    fresh.message.date = now - datetime.timedelta(seconds=10)
    assert _tb._msg_age_secs(fresh) == pytest.approx(10, abs=2)

    stale = _FakeUpdate(_FakeMessage())
    stale.message.date = now - datetime.timedelta(hours=2)
    assert _tb._msg_age_secs(stale) > _tb._STALE_SKIP_SECS
