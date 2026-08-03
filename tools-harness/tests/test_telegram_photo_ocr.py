"""
Regression guard for the telegram photo-analysis bug: handle_photo passed photo_path
into the ocr_text slot of _analyze_screenshot_ocr, so the model reasoned about a
temp-file path instead of the image's contents. handle_photo must now OCR the image
via tools.local_vision and feed real text down.
Hermetic — no network, no real OCR, no Telegram API.
"""

import asyncio
from types import SimpleNamespace

import pytest

import telegram_bot as tb

_CAPTURED = {}


class _FakeBot:
    async def get_file(self):
        return self

    async def download_to_drive(self, path):
        return None


class _FakePhoto:
    def __init__(self, width=800, height=600):
        self.width = width
        self.height = height

    async def get_file(self):
        return _FakeBot()

    async def download_to_drive(self, path):
        return None


class _FakePlaceholder:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text):
        self.edits.append(text)


class _FakeChat:
    def __init__(self):
        self.actions = []

    async def send_action(self, action):
        self.actions.append(action)


class _FakeMessage:
    def __init__(self, caption=None):
        self.photo = [_FakePhoto()]
        self.caption = caption
        self.chat = _FakeChat()
        self.placeholder = _FakePlaceholder()

    async def reply_text(self, text):
        assert text == "Analyzing image…"
        return self.placeholder


class _FakeUpdate:
    def __init__(self, caption=None):
        self.message = _FakeMessage(caption=caption)
        self.effective_user = SimpleNamespace(id=123)
        self.effective_chat = SimpleNamespace(id=456)


def _patch_common(monkeypatch):
    monkeypatch.setattr(tb, "_allowed", lambda update: True)
    monkeypatch.setattr(tb, "_tg_retry", lambda fn, label, **kw: fn())
    monkeypatch.setattr(tb, "log", type("_Log", (), {"info": lambda *a, **k: None,
                                                     "warning": lambda *a, **k: None})())

    def _fake_analyze(query, ocr_text, on_token=None):
        _CAPTURED["query"] = query
        _CAPTURED["ocr_text"] = ocr_text
        return "ANSWER"

    monkeypatch.setattr(tb, "_analyze_screenshot_ocr", _fake_analyze)


@pytest.mark.asyncio
async def test_handle_photo_feeds_real_ocr_text_not_file_path(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        "tools.local_vision.ocr",
        lambda path, lang="eng+fra": [{"text": "HELLO"}, {"text": "WORLD"}, {"text": "42"}],
    )

    update = _FakeUpdate(caption="what does this say?")
    await tb.handle_photo(update, None)

    assert _CAPTURED["ocr_text"] == "HELLO WORLD 42", (
        "analyzer must receive joined OCR text, not the photo file path"
    )
    assert "/" not in _CAPTURED["ocr_text"]


@pytest.mark.asyncio
async def test_handle_photo_empty_ocr_returns_notice(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        "tools.local_vision.ocr",
        lambda path, lang="eng+fra": [],
    )

    update = _FakeUpdate(caption="what does this say?")
    await tb.handle_photo(update, None)

    assert update.message.placeholder.edits == [
        "Couldn't read any text in the image clearly — try again or send a clearer photo."
    ]


@pytest.mark.asyncio
async def test_handle_photo_ocr_error_returns_notice(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        "tools.local_vision.ocr",
        lambda path, lang="eng+fra": [{"error": "missing tesseract"}],
    )

    update = _FakeUpdate(caption="read this")
    await tb.handle_photo(update, None)

    assert update.message.placeholder.edits == [
        "Couldn't read any text in the image clearly — try again or send a clearer photo."
    ]
