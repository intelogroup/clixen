"""
Remaining telegram_bot coverage: the document-agent body (synthesis + conversion +
NO DATA / error-string paths), screenshot capture pipeline (screencapture → OCR →
downscale), screenshot OCR reasoning (streaming, empty-OCR, failure), ugent Q&A
search, reminder firing, and the /start /new /tts /tutor /agent command surfaces.

All LLM/network/fs boundaries mocked; orchestration runs for real.
"""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

import telegram_bot as tb

# ---------------------------------------------------------------------------
# _run_doc_agent — synthesis → conversion, with all boundaries mocked
# ---------------------------------------------------------------------------


@pytest.fixture
def doc_mocks(monkeypatch, tmp_path):
    """Wire _run_doc_agent's internal imports to test doubles."""
    import clients.router as router
    import store.conversation as conv
    import tools.document_create as dc
    import tools.memory_tools as mt

    state = {"body": "# Title\nContent here.", "chat": []}
    monkeypatch.setattr(conv, "get", lambda cid: state["chat"])
    monkeypatch.setattr(router, "_TEMPORAL_RE", type("R", (), {"search": lambda self, q: False})())
    monkeypatch.setattr(mt, "recall_block", lambda q: "")
    monkeypatch.setattr(tb, "_live_evidence", lambda q: "")
    monkeypatch.setattr(tb.harness, "local_chat", lambda user_message, model, tools: state["body"])

    artifacts = {}

    def _fake_create_pdf(body, out):
        artifacts["pdf"] = out
        _write(out, b"%PDF")
        return out

    def _fake_data_to_xlsx(csv_path, out):
        artifacts["xlsx"] = out
        _write(out, b"PK")
        return out

    def _fake_md_to_docx(md, out):
        artifacts["docx"] = out
        _write(out, b"PKdocx")
        return out

    def _fake_md_to_pptx(md, out):
        artifacts["pptx"] = out
        _write(out, b"PKpptx")
        return out

    monkeypatch.setattr(dc, "create_pdf", _fake_create_pdf)
    monkeypatch.setattr(dc, "data_to_xlsx", _fake_data_to_xlsx)
    monkeypatch.setattr(dc, "markdown_to_docx", _fake_md_to_docx)
    monkeypatch.setattr(dc, "markdown_to_pptx", _fake_md_to_pptx)

    def _fake_write_file(path, content):
        _write(path, content.encode())

    monkeypatch.setattr(dc, "_write_file", _fake_write_file)
    from pathlib import Path as _P
    monkeypatch.setattr(tb.os.path, "expanduser", lambda p: str(tmp_path / _P(p).name))
    state["tmp"] = tmp_path
    state["artifacts"] = artifacts
    return state


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def test_run_doc_agent_pdf_default(doc_mocks, monkeypatch):
    monkeypatch.setattr(tb, "_doc_format", lambda q: ("PDF", ".pdf"))
    confirm, path = tb._run_doc_agent("summarize the meeting", "deepseek/x", "chat1", None)
    assert confirm.startswith("Here's your PDF")
    assert path and path.endswith(".pdf")
    assert doc_mocks["artifacts"]["pdf"] == path


def test_run_doc_agent_xlsx_path(doc_mocks, monkeypatch):
    monkeypatch.setattr(tb, "_doc_format", lambda q: ("Excel spreadsheet", ".xlsx"))
    confirm, path = tb._run_doc_agent("make a table", "deepseek/x", "chat1", None)
    assert "Here's your Excel spreadsheet" in confirm
    assert path.endswith(".xlsx")
    # intermediate .csv dropped
    assert not (doc_mocks["tmp"] / "artifact.csv").exists()


def test_run_doc_agent_returns_no_data_message(doc_mocks, monkeypatch):
    monkeypatch.setattr(tb, "_doc_format", lambda q: ("PDF", ".pdf"))
    doc_mocks["body"] = "NO DATA"
    confirm, path = tb._run_doc_agent("unfindable thing", "deepseek/x", "chat1", None)
    assert "couldn't find the data" in confirm
    assert path is None


def test_run_doc_agent_empty_body_after_fence_strip(doc_mocks, monkeypatch):
    monkeypatch.setattr(tb, "_doc_format", lambda q: ("PDF", ".pdf"))
    doc_mocks["body"] = "```\n```"
    confirm, path = tb._run_doc_agent("empty", "deepseek/x", "chat1", None)
    assert path is None


def test_run_doc_agent_converter_error_string_no_file(doc_mocks, monkeypatch):
    monkeypatch.setattr(tb, "_doc_format", lambda q: ("PDF", ".pdf"))
    import tools.document_create as dc
    monkeypatch.setattr(dc, "create_pdf", lambda body, out: "python-pptx is missing")
    confirm, path = tb._run_doc_agent("make pdf", "deepseek/x", "chat1", None)
    assert "couldn't build the PDF" in confirm
    assert path is None


def test_run_doc_agent_converter_raises(doc_mocks, monkeypatch):
    monkeypatch.setattr(tb, "_doc_format", lambda q: ("PDF", ".pdf"))
    import tools.document_create as dc

    def _boom(body, out):
        raise RuntimeError("disk full")

    monkeypatch.setattr(dc, "create_pdf", _boom)
    confirm, path = tb._run_doc_agent("make pdf", "deepseek/x", "chat1", None)
    assert "couldn't build the PDF" in confirm
    assert path is None


def test_run_doc_agent_feeds_conversation_source(doc_mocks, monkeypatch):
    monkeypatch.setattr(tb, "_doc_format", lambda q: ("PDF", ".pdf"))
    doc_mocks["chat"] = [{"role": "user", "content": "remember this detail"}]
    calls = {}

    def _spy_chat(user_message, model, tools):
        calls["prompt"] = user_message
        return doc_mocks["body"]

    monkeypatch.setattr(tb.harness, "local_chat", _spy_chat)
    tb._run_doc_agent("make pdf of earlier", "deepseek/x", "chat1", None)
    assert "remember this detail" in calls["prompt"]
    assert "Recent conversation" in calls["prompt"]


def test_run_doc_agent_fires_on_token(doc_mocks, monkeypatch):
    monkeypatch.setattr(tb, "_doc_format", lambda q: ("PDF", ".pdf"))
    tokens = []
    tb._run_doc_agent("make pdf", "deepseek/x", "chat1", tokens.append)
    assert any("Here's your PDF" in t for t in tokens)


# ---------------------------------------------------------------------------
# _capture_screenshot — screencapture → OCR → downscale
# ---------------------------------------------------------------------------


def test_capture_screencapture_failure_returns_false(tmp_path):
    class _Res:
        returncode = 1

    import subprocess
    orig = subprocess.run
    subprocess.run = staticmethod(lambda *a, **k: _Res())
    try:
        ok, ocr = tb._capture_screenshot(str(tmp_path / "s.jpg"))
    finally:
        subprocess.run = orig
    assert ok is False
    assert ocr == ""


def test_capture_ocr_error_word_yields_empty_ocr(monkeypatch, tmp_path):
    import tools.local_vision as lv
    monkeypatch.setattr(lv, "ocr", lambda p: [{"error": "tesseract missing"}])

    class _Res:
        returncode = 0

    import subprocess
    orig = subprocess.run
    subprocess.run = staticmethod(lambda *a, **k: _Res())
    try:
        ok, ocr = tb._capture_screenshot(str(tmp_path / "s.jpg"))
    finally:
        subprocess.run = orig
    assert ok is True
    assert ocr == ""


def test_capture_pillow_downscale_pipeline(monkeypatch, tmp_path):
    """Full PIL path: png → native → downscale → jpeg, with real files."""
    import tools.local_vision as lv
    from PIL import Image as PILImage

    png = tmp_path / "s.jpg.png"
    png.write_bytes(b"pngbytes")

    class _Img:
        def __init__(self, size=(1600, 900), mode="RGB"):
            self.size = size
            self.mode = mode
            self.saved = None

        def convert(self, mode):
            return self

        def resize(self, new_size, resample):
            self.size = new_size
            return self

        def save(self, path, fmt=None, **kw):
            self.saved = (path, fmt, kw)

    img = _Img()
    monkeypatch.setattr(PILImage, "open", staticmethod(lambda p: img))
    monkeypatch.setattr(PILImage, "Resampling", SimpleNamespace(LANCZOS="lanczos"))
    monkeypatch.setattr(lv, "ocr", lambda p: [{"text": "alpha"}, {"text": "beta"}])
    monkeypatch.setattr("subprocess.run", staticmethod(lambda *a, **k: SimpleNamespace(returncode=0)))

    ok, ocr = tb._capture_screenshot(str(tmp_path / "s.jpg"))
    assert ok is True
    assert ocr == "alpha beta"
    assert img.saved[0] == str(tmp_path / "s.jpg")
    assert img.saved[1] == "JPEG"
    assert img.saved[2]["quality"] == 80
    assert img.size == (800, 450)  # downscaled from 1600x900


def test_capture_pillow_exception_falls_back_to_copy(monkeypatch, tmp_path):
    import tools.local_vision as lv
    from PIL import Image as PILImage
    png = tmp_path / "s.jpg.png"
    png.write_bytes(b"pngbytes")

    class _Exploding:
        def open(self, p):
            raise RuntimeError("corrupt image")

    monkeypatch.setattr(PILImage, "open", staticmethod(_Exploding().open))
    monkeypatch.setattr(lv, "ocr", lambda p: [{"text": "raw"}])
    monkeypatch.setattr("subprocess.run", staticmethod(lambda *a, **k: SimpleNamespace(returncode=0)))

    ok, ocr = tb._capture_screenshot(str(tmp_path / "s.jpg"))
    assert ok is True
    assert ocr == "raw"
    assert (tmp_path / "s.jpg").exists()  # raw png copied to jpeg path


# ---------------------------------------------------------------------------
# _analyze_screenshot_ocr — streaming LLM over OCR text
# ---------------------------------------------------------------------------


def _fake_ollama_module(chunks, raise_after=None):
    import ollama as real

    class _Chunk:
        def __init__(self, content, eval_count=None):
            self.message = SimpleNamespace(content=content)
            self.eval_count = eval_count

    class _Chat:
        def __init__(self):
            self.calls = []

        def __call__(self, model, messages, **kw):
            self.calls.append((model, messages, kw))
            for i, c in enumerate(chunks):
                if raise_after is not None and i >= raise_after:
                    raise RuntimeError("ollama down")
                yield c

    fake = SimpleNamespace(chat=_Chat())
    return fake


def test_analyze_screenshot_ocr_empty_text(monkeypatch):
    result = tb._analyze_screenshot_ocr("what's on screen", "   ")
    assert "Couldn't read any text" in result


def test_analyze_screenshot_ocr_streams_and_collects(monkeypatch):
    fake = _fake_ollama_module([
        SimpleNamespace(message=SimpleNamespace(content="The answer"), eval_count=10),
        SimpleNamespace(message=SimpleNamespace(content=" is 42."), eval_count=5),
    ])
    monkeypatch.setitem(sys.modules, "ollama", fake)
    tokens = []
    result = tb._analyze_screenshot_ocr("what is it", "ocr payload", tokens.append)
    assert result == "The answer is 42."
    assert "".join(tokens) == "The answer is 42."
    model, messages, kw = fake.chat.calls[0]
    assert model == "gemma4:12b-mlx"
    assert kw["stream"] is True
    assert "ocr payload" in messages[0]["content"]


def test_analyze_screenshot_ocr_surfaces_llm_failure(monkeypatch):
    fake = _fake_ollama_module([SimpleNamespace(message=SimpleNamespace(content="x"), eval_count=1)], raise_after=0)
    monkeypatch.setitem(sys.modules, "ollama", fake)
    assert tb._analyze_screenshot_ocr("q", "text") is None


# ---------------------------------------------------------------------------
# _search_ugent — substring over enriched Q&A jsonl
# ---------------------------------------------------------------------------


def test_search_ugent_matches_and_respects_limit(monkeypatch, tmp_path):
    rows = [
        {"enriched": {"diseaseName": "Diabetes", "questionText": "insulin dosing"}},
        {"enriched": {"subject": "diabetes management", "questionText": "A1C targets"}},
        {"enriched": {"system": "endocrinology", "questionText": "oral agents"}},
        {"enriched": {"questionText": "nothing to do with it"}},
    ]
    path = tmp_path / "medicospira-enriched.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(tb, "_UGENT_ENRICHED", str(path))

    hits = tb._search_ugent("diabetes", limit=2)
    assert len(hits) == 2  # capped at limit
    assert all("diabetes" in (r.get("diseaseName") or r.get("subject") or "").lower() for r in hits)


def test_search_ugent_case_insensitive(monkeypatch, tmp_path):
    rows = [{"enriched": {"questionText": "DIABETES ketoacidosis"}}]
    path = tmp_path / "medicospira-enriched.jsonl"
    path.write_text(json.dumps(rows[0]))
    monkeypatch.setattr(tb, "_UGENT_ENRICHED", str(path))
    assert len(tb._search_ugent("Diabetes")) == 1


def test_search_ugent_no_match_returns_empty(monkeypatch, tmp_path):
    path = tmp_path / "medicospira-enriched.jsonl"
    path.write_text(json.dumps({"enriched": {"questionText": "oncology"}}))
    monkeypatch.setattr(tb, "_UGENT_ENRICHED", str(path))
    assert tb._search_ugent("cardiology") == []


# ---------------------------------------------------------------------------
# _fire_reminder
# ---------------------------------------------------------------------------


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, text=None):
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_fire_reminder_sends_via_bot_ref(monkeypatch):
    bot = _FakeBot()
    monkeypatch.setattr(tb, "_bot_app_ref", SimpleNamespace(bot=bot))
    await tb._fire_reminder("chat42", "drink water")
    assert bot.sent == [("chat42", "Reminder: drink water")]


@pytest.mark.asyncio
async def test_fire_reminder_noop_when_no_bot_ref(monkeypatch):
    monkeypatch.setattr(tb, "_bot_app_ref", None)
    await tb._fire_reminder("chat42", "drink water")  # must not raise


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


class _CmdMsg:
    def __init__(self):
        self.texts = []
        self.chat = SimpleNamespace(send_action=lambda a: _noop_coro())

    async def reply_text(self, text):
        self.texts.append(text)


async def _noop_coro():
    return None


class _Placeholder:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text):
        self.edits.append(text)


class _CmdUpdate:
    def __init__(self, chat_id="chat1", user_id=123):
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_user = SimpleNamespace(id=user_id)
        self.message = _CmdMsg()


class _CmdCtx:
    def __init__(self, args=()):
        self.args = list(args)
        self.bot = _FakeBot()


@pytest.mark.asyncio
async def test_cmd_start_help(monkeypatch):
    monkeypatch.setattr(tb, "ALLOWED_USER_IDS", set())
    update = _CmdUpdate()
    await tb.cmd_start(update, _CmdCtx())
    assert "Gemma4 harness online" in update.message.texts[0]


@pytest.mark.asyncio
async def test_cmd_new_clears_conversation(monkeypatch):
    import store.conversation as conv
    cleared = []
    monkeypatch.setattr(conv, "clear", lambda cid: cleared.append(cid))
    monkeypatch.setattr(tb, "ALLOWED_USER_IDS", set())
    update = _CmdUpdate(chat_id="chat77")
    await tb.cmd_new(update, _CmdCtx())
    assert cleared == ["chat77"]
    assert update.message.texts == ["Started a new conversation."]


@pytest.mark.asyncio
async def test_cmd_tts_toggle_default_to_on(monkeypatch):
    monkeypatch.setattr(tb, "ALLOWED_USER_IDS", set())
    tb._tts_enabled.clear()
    update = _CmdUpdate(chat_id="tts_test")
    await tb.cmd_tts(update, _CmdCtx())
    assert update.message.texts[0] == "TTS ON"


@pytest.mark.asyncio
async def test_cmd_tts_toggle_back_to_off(monkeypatch):
    monkeypatch.setattr(tb, "ALLOWED_USER_IDS", set())
    tb._tts_enabled["tts_test2"] = True  # pre-set ON
    update = _CmdUpdate(chat_id="tts_test2")
    await tb.cmd_tts(update, _CmdCtx())
    assert update.message.texts[0] == "TTS OFF"


@pytest.mark.asyncio
async def test_cmd_tts_dispatches(monkeypatch):
    called = {}
    monkeypatch.setattr(tb, "ALLOWED_USER_IDS", set())
    monkeypatch.setattr(tb, "_mark_inflight", lambda c, q: None)
    monkeypatch.setattr(tb, "_clear_inflight", lambda: None)

    async def fake_tts(update, query, chat_id):
        called["q"] = query
        called["cid"] = chat_id
        return "resp", "m", "casual"

    monkeypatch.setattr(tb, "_tts_and_send", fake_tts)
    await tb.cmd_tts(_CmdUpdate(), _CmdCtx(["tell me a joke"]))
    assert called == {"q": "tell me a joke", "cid": "chat1"}


@pytest.mark.asyncio
async def test_cmd_tutor_status_not_running(monkeypatch):
    monkeypatch.setattr(tb, "_tutor_thread", None)
    update = _CmdUpdate()
    await tb.cmd_tutor(update, _CmdCtx(["status"]))
    assert "STOPPED" in update.message.texts[0]


@pytest.mark.asyncio
async def test_cmd_tutor_stop_not_running(monkeypatch):
    monkeypatch.setattr(tb, "_tutor_thread", None)
    update = _CmdUpdate()
    await tb.cmd_tutor(update, _CmdCtx(["stop"]))
    assert update.message.texts[0] == "Tutor not running."


@pytest.mark.asyncio
async def test_cmd_tutor_bad_subcommand(monkeypatch):
    monkeypatch.setattr(tb, "_tutor_thread", None)
    update = _CmdUpdate()
    await tb.cmd_tutor(update, _CmdCtx(["frobnicate"]))
    assert update.message.texts[0] == "Usage: /tutor {start|stop|status}"


@pytest.mark.asyncio
async def test_cmd_agent_usage_when_no_args(monkeypatch):
    monkeypatch.setattr(tb, "ALLOWED_USER_IDS", set())
    update = _CmdUpdate()
    await tb.cmd_agent(update, _CmdCtx())
    assert update.message.texts[0].startswith("Usage: /agent")


@pytest.mark.asyncio
async def test_cmd_agent_unpacks_run_result_tuple(monkeypatch):
    """run_for_messaging returns (result, model, intent) — cmd_agent must not
    send the tuple repr to the user (regression: it used `result[:4000]` on the
    tuple, and _send_images_in_text re.findall crashed on a tuple)."""
    monkeypatch.setattr(tb, "ALLOWED_USER_IDS", set())

    def fake_run(query, **kw):
        return "final answer", "deepseek/x", "agent_task"

    monkeypatch.setattr(tb.harness, "run_for_messaging", fake_run)
    monkeypatch.setattr(tb, "_send_images_in_text", _noop_images)

    class _Msg(_CmdMsg):
        def __init__(self):
            super().__init__()
            self.placeholder = _Placeholder()

        async def reply_text(self, text):
            self.texts.append(text)
            return self.placeholder

    update = _CmdUpdate()
    update.message = _Msg()
    await tb.cmd_agent(update, _CmdCtx(["do the thing"]))
    assert "final answer" in update.message.placeholder.edits[-1]
    assert "deepseek" not in update.message.placeholder.edits[-1]


@pytest.mark.asyncio
async def test_cmd_learn_unpacks_run_result_tuple(monkeypatch):
    """Same tuple-unpacking regression for /learn — must send the response
    string, not the (result, model, intent) tuple."""
    monkeypatch.setattr(tb, "ALLOWED_USER_IDS", set())
    monkeypatch.setattr(tb, "_search_ugent", lambda topic, limit=5: [{"questionText": "q?", "correctAnswer": "a", "explanation": "e"}])

    def fake_run(query, **kw):
        return "Here's your lecture", "deepseek/x", "casual"

    monkeypatch.setattr(tb.harness, "run_for_messaging", fake_run)
    monkeypatch.setattr(tb, "_send_long", _fake_send_long)

    update = _CmdUpdate()
    await tb.cmd_learn(update, _CmdCtx(["diabetes"]))
    assert update.message.texts[0] == "Here's your lecture"


async def _noop_images(update, text):
    return None


async def _fake_send_long(update, text):
    update.message.texts.append(text)
