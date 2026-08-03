"""
Deep telegram_bot paths: document format parsing, content-query noise stripping,
code-fence stripping, TTS chunk synthesis (char-split, md-strip, sentinel), the
_tts_and_send pipeline (budget, progress-label stripping, doc vs generic dispatch),
and the _dispatch_query screenshot fast-path with token streaming.

Mocks replace the network/LLM boundaries (harness.run_for_messaging, classify,
kokoro, websearch); the bot's own orchestration logic runs for real.
"""

import asyncio
import re
import time

import pytest

import telegram_bot as tb

# ---------------------------------------------------------------------------
# _doc_format — output-format selection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("q,fmt,ext", [
    ("make an excel of the data", "Excel spreadsheet", ".xlsx"),
    ("as a csv", "Excel spreadsheet", ".xlsx"),
    ("spreadsheet please", "Excel spreadsheet", ".xlsx"),
    ("word doc of this", "Word document", ".docx"),
    ("as a word document", "Word document", ".docx"),
    ("powerpoint with 5 slides", "presentation", ".pptx"),
    ("slides for the meeting", "presentation", ".pptx"),
    ("a plain pdf", "PDF", ".pdf"),
    ("default format", "PDF", ".pdf"),
    ("EXCEL UPPERCASE", "Excel spreadsheet", ".xlsx"),
])
def test_doc_format(q, fmt, ext):
    assert tb._doc_format(q) == (fmt, ext)


def test_doc_format_prefers_excel_over_word_docx_in_query():
    # "word" alone must not win when excel is present
    assert tb._doc_format("excel not word") == ("Excel spreadsheet", ".xlsx")


# ---------------------------------------------------------------------------
# _strip_fences — code fence removal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,clean", [
    ("```csv\na,b\n1,2\n```", "a,b\n1,2"),
    ("```python\nprint('hi')\n```", "print('hi')"),
    ("```\njust text\n```", "just text"),
    ("no fence here", "no fence here"),
    (" ```csv\nrow\n``` ", "row"),
])
def test_strip_fences(raw, clean):
    assert tb._strip_fences(raw) == clean


def test_strip_fences_keeps_leading_content_before_fence():
    raw = "intro line\n```lang\nbody\n```"
    assert tb._strip_fences(raw) == "intro line\n```lang\nbody\n```"  # only strips when WHOLE text is fenced


# ---------------------------------------------------------------------------
# _content_query — format-word noise stripping for web search
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,clean", [
    ("today's matches as an excel sheet", "today's matches as an"),
    ("world cup csv download", "world cup download"),
    ("price list in spreadsheet", "price list in"),
    ("make a pdf", "make a"),  # only the format word removed
    ("recipe pdf", "recipe"),
    ("constitution of india", "constitution of india"),  # untouched
    ("EXCEL sheet of temperatures", "of temperatures"),
])
def test_content_query_strips_format_noise(raw, clean):
    assert tb._content_query(raw) == clean


def test_content_query_returns_original_when_everything_stripped():
    assert tb._content_query("excel spreadsheet csv") == "excel spreadsheet csv"


# ---------------------------------------------------------------------------
# _age_label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("secs,label", [
    (30, "0 min ago"),
    (3599, "59 min ago"),
    (3600, "1h 0m ago"),
    (5400, "1h 30m ago"),
    (7200, "2h 0m ago"),
])
def test_age_label(secs, label):
    assert tb._age_label(secs) == label


# ---------------------------------------------------------------------------
# _synthesize_chunks — queue consumption, md strip, 150-char hard split
# ---------------------------------------------------------------------------


class _FakeKokoro:
    def __init__(self):
        self.pieces = []

    def create(self, text, voice=None, speed=1.0, lang="en-us"):
        self.pieces.append((text, voice))
        import numpy as np
        return np.zeros((100,), dtype=np.float32), 24000


async def _feed(queue, *items):
    for it in items:
        await queue.put(it)


@pytest.mark.asyncio
async def test_synthesize_chunks_hard_splits_over_150_chars(monkeypatch):
    fake = _FakeKokoro()
    monkeypatch.setattr(tb, "_get_kokoro", lambda: fake)
    q = asyncio.Queue()
    task = asyncio.create_task(tb._synthesize_chunks(q))
    await _feed(q, "word " * 50)  # ~250 chars → must split past the 150 cap
    await q.put(None)
    mp3 = await task

    assert mp3 is not None
    total_chars = sum(len(t) for t, _ in fake.pieces)
    # 50 "word" tokens + 48 single spaces (49 spaces minus 1 dropped at the
    # piece-boundary, since the hard split emits pieces with no trailing space)
    assert total_chars == 4 * 50 + 48
    assert all(len(t) <= 150 for t, _ in fake.pieces), "no single piece exceeds 150 chars"


@pytest.mark.asyncio
async def test_synthesize_chunks_strips_md_and_uses_voice(monkeypatch):
    fake = _FakeKokoro()
    monkeypatch.setattr(tb, "_get_kokoro", lambda: fake)
    q = asyncio.Queue()
    task = asyncio.create_task(tb._synthesize_chunks(q, voice="af_bella"))
    await _feed(q, "**bold** *it* sentence.", None)
    mp3 = await task

    assert mp3 is not None
    assert (len(fake.pieces)) == 1
    text, voice = fake.pieces[0]
    assert text == "bold it sentence."
    assert voice == "af_bella"


@pytest.mark.asyncio
async def test_synthesize_chunks_returns_none_when_no_audio(monkeypatch):
    fake = _FakeKokoro()
    monkeypatch.setattr(tb, "_get_kokoro", lambda: fake)
    q = asyncio.Queue()
    task = asyncio.create_task(tb._synthesize_chunks(q))
    await q.put(None)
    assert await task is None


@pytest.mark.asyncio
async def test_synthesize_chunks_survives_single_bad_piece(monkeypatch):
    class _Flaky:
        def create(self, text, **kw):
            if "boom" in text:
                raise RuntimeError("phoneme overflow")
            import numpy as np
            return np.zeros((10,), dtype=np.float32), 24000

    monkeypatch.setattr(tb, "_get_kokoro", lambda: _Flaky())
    q = asyncio.Queue()
    task = asyncio.create_task(tb._synthesize_chunks(q))
    await _feed(q, "good one", "boom text", "another", None)
    mp3 = await task
    assert mp3 is not None  # bad piece skipped, good ones still synthesized


# ---------------------------------------------------------------------------
# _tts_and_send — full pipeline with mocked boundaries
# ---------------------------------------------------------------------------


class _FakeMsg:
    def __init__(self):
        self.replied_texts = []
        self.replied_photos = []
        self.replied_audio = []
        self.replied_docs = []

    async def reply_text(self, text):
        self.replied_texts.append(text)

    async def reply_photo(self, photo=None):
        self.replied_photos.append(photo)

    async def reply_audio(self, audio=None):
        self.replied_audio.append(audio)

    async def reply_document(self, document=None):
        self.replied_docs.append(document)


class _FakeUpdate:
    def __init__(self):
        self.message = _FakeMsg()


def _cls(intent, model="deepseek/deepseek-v4-flash", hint=None, source="router"):
    return type("Cls", (), {"intent": intent, "model": model,
                            "specialist_hint": hint, "source": source})()


@pytest.mark.asyncio
async def test_tts_and_send_generic_path_streams_and_returns(monkeypatch):
    state = {"harness_called": None}
    fake_msg = _FakeMsg()
    update = _FakeUpdate()
    update.message = fake_msg

    def fake_run_for_messaging(query, **kw):
        state["harness_called"] = kw
        on_token = kw["on_token"]
        for tok in ["Hello", " world", ". ", "This is a test reply", "."]:
            on_token(tok)
        return "Hello world. This is a test reply.", "deepseek/x", "casual"

    monkeypatch.setattr(tb, "classify_message", lambda q, channel=None: _cls("casual"))
    monkeypatch.setattr(tb.harness, "run_for_messaging", fake_run_for_messaging)
    monkeypatch.setattr(tb, "_synthesize_chunks", _sink_synth)
    monkeypatch.setattr(tb, "_send_images_in_text", _fake_noop)

    response, model, intent = await tb._tts_and_send(update, "hi", "chat1")
    assert response == "Hello world. This is a test reply."
    assert model == "deepseek/x"
    assert intent == "casual"
    assert state["harness_called"]["chat_id"] == "chat1"
    assert state["harness_called"]["intent"] == "casual"
    assert fake_msg.replied_texts == ["Hello world. This is a test reply.\n\n— deepseek/x"]


async def _sink_synth(queue, voice="af_heart"):
    while True:
        s = await queue.get()
        if s is None:
            return None


async def _fake_noop(update, text):
    return None


@pytest.mark.asyncio
async def test_tts_and_send_strips_tool_progress_labels(monkeypatch):
    """⚙️ Running <tool>… labels injected by clients must NOT reach TTS."""
    from chat_ui import _sentence_boundary

    state = {"queue": [], "done": False}

    async def recording_synth(queue, voice="af_heart"):
        while True:
            s = await queue.get()
            if s is None:
                state["done"] = True
                return None
            state["queue"].append(s)

    def fake_run(query, **kw):
        on_token = kw["on_token"]
        # label appended mid-stream, then a real sentence
        on_token("\n\n⚙️ Running ask_docs_agent…")
        on_token("The answer is 42.")
        return "The answer is 42.", "m", "casual"

    monkeypatch.setattr(tb, "classify_message", lambda q, channel=None: _cls("casual"))
    monkeypatch.setattr(tb.harness, "run_for_messaging", fake_run)
    monkeypatch.setattr(tb, "_synthesize_chunks", recording_synth)
    monkeypatch.setattr(tb, "_send_images_in_text", _fake_noop)

    await tb._tts_and_send(_FakeUpdate(), "q", "chat1")
    assert state["done"] is True
    assert state["queue"] == ["The answer is 42."]


@pytest.mark.asyncio
async def test_tts_and_send_enforces_char_budget(monkeypatch):
    state = {"chars": 0}

    async def counting_synth(queue, voice="af_heart"):
        while True:
            s = await queue.get()
            if s is None:
                return None
            state["chars"] += len(s)

    def fake_run(query, **kw):
        on_token = kw["on_token"]
        on_token("word " * 600)  # ~3000 chars > 2000 budget
        on_token("tail.")
        return "x", "m", "casual"

    monkeypatch.setattr(tb, "classify_message", lambda q, channel=None: _cls("casual"))
    monkeypatch.setattr(tb.harness, "run_for_messaging", fake_run)
    monkeypatch.setattr(tb, "_synthesize_chunks", counting_synth)
    monkeypatch.setattr(tb, "_send_images_in_text", _fake_noop)

    await tb._tts_and_send(_FakeUpdate(), "q", "chat1")
    assert state["chars"] <= tb._MAX_TTS_TOTAL_CHARS


@pytest.mark.asyncio
async def test_tts_and_send_document_path_uses_doc_agent_and_sends_file(monkeypatch):
    fake_msg = _FakeMsg()
    update = _FakeUpdate()
    update.message = fake_msg

    def fake_doc_agent(query, model, chat_id, on_token):
        from pathlib import Path as _P
        _P("/tmp/fake_doc.pdf").write_bytes(b"%PDF")
        on_token("Document ready.")
        return "Document ready.", "/tmp/fake_doc.pdf"

    monkeypatch.setattr(tb, "classify_message", lambda q, channel=None: _cls("document", hint="pdf"))
    monkeypatch.setattr(tb, "_run_doc_agent", fake_doc_agent)
    monkeypatch.setattr(tb, "_synthesize_chunks", _sink_synth)
    monkeypatch.setattr(tb, "_send_images_in_text", _fake_noop)

    response, model, intent = await tb._tts_and_send(update, "make a pdf", "chat1")
    assert model == "doc-fastpath"
    assert intent == "document"
    assert fake_msg.replied_docs  # document delivered
    assert fake_msg.replied_texts[0].startswith("Document ready.")


@pytest.mark.asyncio
async def test_tts_and_send_appends_stale_label(monkeypatch):
    fake_msg = _FakeMsg()
    update = _FakeUpdate()
    update.message = fake_msg

    def fake_run(query, **kw):
        return "resp", "m", "casual"

    monkeypatch.setattr(tb, "classify_message", lambda q, channel=None: _cls("casual"))
    monkeypatch.setattr(tb.harness, "run_for_messaging", fake_run)
    monkeypatch.setattr(tb, "_synthesize_chunks", _sink_synth)
    monkeypatch.setattr(tb, "_send_images_in_text", _fake_noop)

    await tb._tts_and_send(update, "q", "chat1", stale_label="5 min ago")
    assert "sent 5 min ago" in fake_msg.replied_texts[0]


@pytest.mark.asyncio
async def test_tts_and_send_propagates_harness_failure(monkeypatch):
    def fake_run(query, **kw):
        raise RuntimeError("harness exploded")

    monkeypatch.setattr(tb, "classify_message", lambda q, channel=None: _cls("casual"))
    monkeypatch.setattr(tb.harness, "run_for_messaging", fake_run)
    monkeypatch.setattr(tb, "_synthesize_chunks", _sink_synth)

    with pytest.raises(RuntimeError, match="harness exploded"):
        await tb._tts_and_send(_FakeUpdate(), "q", "chat1")


# ---------------------------------------------------------------------------
# _dispatch_query — screenshot fast-path + generic fallback
# ---------------------------------------------------------------------------


class _FakeChat:
    async def send_action(self, action):
        pass


class _Placeholder:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text):
        self.edits.append(text)


class _ScreenshotMsg(_FakeMsg):
    def __init__(self):
        super().__init__()
        self.chat = _FakeChat()
        self.edits = []
        self.photo = None

    async def reply_text(self, text):
        self.replied_texts.append(text)
        self.placeholder = _Placeholder()
        return self.placeholder

    async def reply_photo(self, photo=None):
        self.photo = photo

    async def edit_text(self, text):
        self.edits.append(text)


class _ScreenshotUpdate:
    def __init__(self):
        self.message = _ScreenshotMsg()


def _fake_analysis(query, ocr_text, on_token=None):
    if on_token:
        on_token("The screen")
        on_token(" shows 42.")
    return "The screen shows 42."


@pytest.mark.asyncio
async def test_dispatch_query_screenshot_path(monkeypatch, tmp_path):
    import store.conversation as conv
    appended = []
    monkeypatch.setattr(conv, "append", lambda cid, role, content: appended.append((role, content)))
    monkeypatch.setattr(conv, "get", lambda cid: [])

    monkeypatch.setattr(tb, "_is_screenshot_request", lambda q: True)
    monkeypatch.setattr(tb, "_wants_photo_sent", lambda q: False)
    monkeypatch.setattr(tb, "_capture_screenshot", lambda p: (True, "raw ocr here"))
    monkeypatch.setattr(tb, "_analyze_screenshot_ocr", _fake_analysis)
    monkeypatch.setattr(tb, "_SCREENSHOT_CACHE_DIR", tmp_path)

    update = _ScreenshotUpdate()
    await tb._dispatch_query(update, "take a screenshot", "chat1", 5.0)

    assert update.message.photo is None  # analyze-only → no photo
    assert update.message.placeholder.edits[-1] == "The screen shows 42."  # final edit = analysis
    assert any(role == "assistant" and "The screen shows 42." in content for role, content in appended)
    assert any(role == "assistant" and tb._SCREENSHOT_OCR_MARKER in content for role, content in appended)


@pytest.mark.asyncio
async def test_dispatch_query_screenshot_failure_replies_error(monkeypatch, tmp_path):
    update = _ScreenshotUpdate()
    monkeypatch.setattr(tb, "_is_screenshot_request", lambda q: True)
    monkeypatch.setattr(tb, "_capture_screenshot", lambda p: (False, ""))
    monkeypatch.setattr(tb, "_SCREENSHOT_CACHE_DIR", tmp_path)

    await tb._dispatch_query(update, "take a screenshot", "chat1", 5.0)
    assert update.message.replied_texts[-1] == "Failed to capture Mac screenshot."


@pytest.mark.asyncio
async def test_dispatch_query_screenshot_sends_photo_when_requested(monkeypatch, tmp_path):
    monkeypatch.setattr(tb, "_is_screenshot_request", lambda q: True)
    monkeypatch.setattr(tb, "_wants_photo_sent", lambda q: True)
    monkeypatch.setattr(tb, "_analyze_screenshot_ocr", _fake_analysis)
    monkeypatch.setattr(tb, "_SCREENSHOT_CACHE_DIR", tmp_path)

    def _capture(path):
        import os as _os
        with _os.fdopen(_os.open(path, _os.O_CREAT | _os.O_WRONLY), "wb") as f:
            f.write(b"fake image bytes")
        return True, "ocr"

    monkeypatch.setattr(tb, "_capture_screenshot", _capture)

    # patch conv to avoid touching the real store
    import store.conversation as conv
    monkeypatch.setattr(conv, "append", lambda cid, role, content: None)
    monkeypatch.setattr(conv, "get", lambda cid: [])

    update = _ScreenshotUpdate()
    await tb._dispatch_query(update, "take a screenshot and send it", "chat1", 5.0)
    assert update.message.photo is not None


@pytest.mark.asyncio
async def test_dispatch_query_generic_path_marks_and_clears_inflight(monkeypatch):
    calls = []
    monkeypatch.setattr(tb, "_is_screenshot_request", lambda q: False)
    monkeypatch.setattr(tb, "_is_screenshot_continuation", lambda q, c: False)

    async def fake_tts_and_send(update, query, chat_id, stale_label=None):
        from tools import inflight_tracker as _inf
        calls.append(("in_flight", _inf._file("telegram").exists()))
        return "resp", "m", "casual"

    monkeypatch.setattr(tb, "_tts_and_send", fake_tts_and_send)
    monkeypatch.setattr(tb, "_mark_inflight", lambda cid, q: calls.append(("mark", cid)))
    monkeypatch.setattr(tb, "_clear_inflight", lambda: calls.append(("clear",)))

    update = _ScreenshotUpdate()
    await tb._dispatch_query(update, "hello", "chat1", 3.0)
    assert ("mark", "chat1") in calls
    assert ("clear",) in calls
