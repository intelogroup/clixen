"""
Telegram bot gateway — receives messages from phone and runs them through
the local LLM harness (gemma4 + tools).

Setup:
    1. Create a bot via @BotFather on Telegram, copy the token.
    2. Add TELEGRAM_BOT_TOKEN to .env
    3. pip install python-telegram-bot
    4. python telegram_bot.py

Usage on phone:
    Send any text → gets answered by Gemma4 via harness.run()
    Send a voice note → transcript gets passed to harness.run()
    /tts <text>  → responds with spoken audio (.ogg)
"""

import asyncio
import datetime
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# faster-whisper model — loaded once from local cache, lives for the process lifetime
# Path is pinned so it never attempts a HuggingFace network call
from faster_whisper import WhisperModel as _WhisperModel

_whisper = None
_whisper_lock = threading.Lock()


def _get_whisper():
    global _whisper
    if _whisper is None:
        with _whisper_lock:
            if _whisper is None:
                _whisper = _WhisperModel("Systran/faster-whisper-base", device="cpu", compute_type="int8", local_files_only=True)
    return _whisper


_kb_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kb-store")

# Kokoro TTS — loaded once at startup, reused for all subsequent calls.
# CPUExecutionProvider by default: onnxruntime's CoreML EP is not officially
# supported via the Python API on Apple Silicon and has caused intermittent
# malloc heap-corruption crashes (SIGABRT/SIGSEGV) in this process under
# concurrent/threaded use — confirmed via macOS crash report (malloc_zone_error
# inside an unrelated SSL read, meaning heap corruption occurred earlier from
# a native library). Set ONNX_PROVIDER=CoreMLExecutionProvider to opt back in
# if you want the speed and can tolerate occasional bot crashes.
_kokoro = None
_kokoro_lock = threading.Lock()


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        with _kokoro_lock:
            if _kokoro is None:
                os.environ.setdefault("ONNX_PROVIDER", "CPUExecutionProvider")
                log.info("Kokoro: using %s", os.environ["ONNX_PROVIDER"])
                from kokoro_onnx import Kokoro

                _kokoro = Kokoro(os.environ["KOKORO_ONNX_PATH"], os.environ["KOKORO_VOICES_PATH"])
    return _kokoro


from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

_scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url="sqlite:///reminders.db")}
)
_bot_app_ref = None  # set in main() after app is built

from tools import inflight_tracker as _inflight


def _mark_inflight(chat_id: str, query: str) -> None:
    _inflight.mark("telegram", chat_id, query)


def _clear_inflight() -> None:
    _inflight.clear("telegram")


async def _fire_reminder(chat_id: str, text: str):
    """Called by APScheduler when a reminder fires."""
    if _bot_app_ref is not None:
        await _bot_app_ref.bot.send_message(chat_id=chat_id, text=f"Reminder: {text}")


_tts_enabled: dict[str, bool] = {}  # per-chat TTS toggle, default OFF


from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from telegram.request import HTTPXRequest

_TG_NET_ERRORS = (NetworkError, TimedOut)


async def _tg_retry(fn, label="", attempts=4):
    for i in range(attempts):
        try:
            return await fn()
        except _TG_NET_ERRORS:
            if i == attempts - 1:
                raise
            await asyncio.sleep(1.5 * (2 ** i))


import harness
from clients.router import classify_message, warm_classifier

from internet_monitor import InternetMonitor
from log_config import setup_logging

log = setup_logging(__name__, log_file="telegram_bot.log", default_level=logging.DEBUG)

# Silence noisy HTTP/telegram debug lines from stderr — keep only WARNING+
for _noisy in ("httpx", "httpcore", "telegram", "hpack"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS: set[int] = set()  # empty = allow all; add your Telegram user ID to restrict


# ---------------------------------------------------------------------------
# Tutor daemon bridge — polls opencode.db, forwards assistant msgs to Telegram
# ---------------------------------------------------------------------------

_TUTOR_DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")
_tutor_stop = threading.Event()
_tutor_thread: threading.Thread | None = None
_tutor_spoken: set[int] = set()
_TUTOR_POLL = 0.5


def _tutor_worker(chat_id: str, bot):
    conn = sqlite3.connect(_TUTOR_DB_PATH, check_same_thread=False)
    session_id = None
    _tutor_spoken.clear()

    while not _tutor_stop.is_set():
        try:
            if session_id is None:
                row = conn.execute(
                    "SELECT session_id FROM message ORDER BY time_created DESC LIMIT 1"
                ).fetchone()
                session_id = row[0] if row else None
                if session_id:
                    rows = conn.execute(
                        "SELECT id FROM message WHERE session_id=? AND json_extract(data,'$.role')='assistant'",
                        (session_id,),
                    ).fetchall()
                    _tutor_spoken.update(mid for (mid,) in rows)
                time.sleep(_TUTOR_POLL)
                continue

            rows = conn.execute(
                """
                SELECT m.id, p.data FROM message m
                JOIN part p ON p.message_id = m.id
                WHERE m.session_id=? AND json_extract(m.data,'$.role')='assistant'
                AND json_extract(m.data,'$.finish')='stop'
                ORDER BY m.id
                """,
                (session_id,),
            ).fetchall()

            for mid, data_json in rows:
                if mid in _tutor_spoken:
                    continue
                part = json.loads(data_json)
                if part.get("type") == "text":
                    text = part.get("text", "").strip()
                    if text:
                        _tutor_spoken.add(mid)
                        topic_keywords = " ".join(text.split()[:15])
                        related = _search_ugent(topic_keywords, limit=2)
                        extra = ""
                        if related:
                            bits = []
                            for q in related:
                                bits.append(
                                    f"Q: {q.get('questionText','')[:250]}\n"
                                    f"Ans: {q.get('correctAnswer','')}\n"
                                    f"Exp: {q.get('explanation','')[:200]}"
                                )
                            extra = "\n\nRelated Q&A:\n" + "\n---\n".join(bits)
                        msg = f"Tutor:\n{text[:2500]}{extra}"
                        loop = asyncio.get_event_loop()
                        asyncio.run_coroutine_threadsafe(
                            bot.send_message(chat_id=chat_id, text=msg[:4000]),
                            loop,
                        )
            time.sleep(_TUTOR_POLL)
        except sqlite3.OperationalError:
            time.sleep(_TUTOR_POLL)
        except Exception as e:
            log.warning("Tutor worker error: %s", e)
            time.sleep(2)
    conn.close()


# ---------------------------------------------------------------------------
# Ugent question search
# ---------------------------------------------------------------------------

_UGENT_ENRICHED = os.path.expanduser("~/Developer/ugent-app/data/medicospira-enriched.jsonl")


def _search_ugent(topic: str, limit: int = 5) -> list[dict]:
    results = []
    t = topic.lower()
    with open(_UGENT_ENRICHED) as f:
        for line in f:
            e = json.loads(line).get("enriched", {})
            haystack = " ".join(
                filter(
                    None,
                    [
                        e.get("diseaseName", ""),
                        e.get("subject", ""),
                        e.get("system", ""),
                        e.get("questionText", ""),
                        e.get("educationalObjective", ""),
                    ],
                )
            ).lower()
            if t in haystack:
                results.append(e)
                if len(results) >= limit:
                    break
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _allowed(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return update.effective_user.id in ALLOWED_USER_IDS


_MD_RE = __import__("re").compile(r"(\*{1,3}|_{1,3}|`{1,3}|~~|#{1,6}\s?)")


def _strip_md(text: str) -> str:
    """Remove common markdown tokens so Telegram/TTS receive clean plain text."""
    return _MD_RE.sub("", text).strip()


async def _send_long(update: Update, text: str):
    """Telegram messages are capped at 4096 chars — chunk if needed.

    Retries each chunk on transient network errors (NetworkError/TimedOut) —
    by the time this runs, harness.run() already did the expensive work
    (LLM call, tool loop), so a mid-air connection blip to Telegram's API
    shouldn't drop the response the user is waiting for.
    """
    clean = _strip_md(text)
    for i in range(0, len(clean), 4096):
        chunk = clean[i : i + 4096]
        for attempt in range(3):
            try:
                await update.message.reply_text(chunk)
                break
            except (NetworkError, TimedOut):
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))




# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Gemma4 harness online.\n"
        "Send any text to query the LLM.\n"
        "Send a voice note to transcribe + query.\n"
        "/tts <text> to get a spoken reply.\n"
        "/usage to see today's cloud token usage.\n"
        "/tutor {start|stop|status} to forward opencode tutor responses here."
    )


async def cmd_usage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show today's cloud token usage against the daily budget."""
    from clients.cost_guard import today_usage, DAILY_TOKEN_BUDGET
    data = today_usage()
    total = data["prompt_tokens"] + data["completion_tokens"]
    pct = 100 * total / DAILY_TOKEN_BUDGET
    await update.message.reply_text(
        f"Today's cloud usage: {total:,}/{DAILY_TOKEN_BUDGET:,} tokens ({pct:.0f}%)\n"
        f"prompt: {data['prompt_tokens']:,}  completion: {data['completion_tokens']:,}\n"
        f"(budget is advisory — cloud keeps being used past it, see harness.py local_chat())"
    )


async def cmd_tutor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global _tutor_thread, _tutor_stop

    sub = (ctx.args[0] if ctx.args else "status").lower()
    chat_id = str(update.effective_chat.id)

    if sub == "stop":
        if _tutor_thread and _tutor_thread.is_alive():
            _tutor_stop.set()
            _tutor_thread.join(timeout=3)
            _tutor_thread = None
            await update.message.reply_text("Tutor daemon stopped.")
        else:
            await update.message.reply_text("Tutor not running.")
        return

    if sub == "status":
        alive = _tutor_thread is not None and _tutor_thread.is_alive()
        await update.message.reply_text(
            f"Tutor: {'RUNNING' if alive else 'STOPPED'}\n"
            f"DB: {_TUTOR_DB_PATH}"
        )
        return

    if sub == "start":
        if _tutor_thread and _tutor_thread.is_alive():
            await update.message.reply_text("Tutor already running.")
            return
        _tutor_stop.clear()
        _tutor_thread = threading.Thread(
            target=_tutor_worker, args=(chat_id, ctx.bot), daemon=True
        )
        _tutor_thread.start()
        await update.message.reply_text(
            "Tutor started — forwarding new assistant messages here."
        )
        return

    await update.message.reply_text("Usage: /tutor {start|stop|status}")


async def cmd_learn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(ctx.args).strip()
    if not topic:
        await update.message.reply_text("Usage: /learn <topic>\nExample: /learn diabetes treatment")
        return

    await update.message.chat.send_action("typing")
    questions = await asyncio.to_thread(_search_ugent, topic)
    if not questions:
        await update.message.reply_text(f"No questions found on '{topic}'.")
        return

    context = "\n\n".join(
        f"Q{i+1}: {q.get('questionText','')[:600]}\nAnswer: {q.get('correctAnswer','')}\n"
        f"Explanation: {q.get('explanation','')[:400]}"
        for i, q in enumerate(questions)
    )

    task = (
        f"You are a USMLE tutor. Give a concise lecture on '{topic}' using these "
        f"source questions. Format: pathophysiology overview → key clinical pearls → "
        f"2 practice questions with answers. Keep under 2000 chars, no markdown except **bold** for terms.\n\n"
        f"Source questions:\n{context}"
    )

    try:
        chat_id = str(update.effective_chat.id)
        result, _, _ = await asyncio.to_thread(harness.run_for_messaging, task, on_token=None, chat_id=chat_id)
        if result:
            await _send_long(update, result)
        else:
            await update.message.reply_text("Lecture generation failed.")
    except Exception as e:
        log.error("learn failed: %s", e, exc_info=True)
        await update.message.reply_text(f"Error: {e}")


async def _synthesize_chunks(sentence_q: asyncio.Queue, voice: str = "af_heart") -> str | None:
    """
    Consume sentences from sentence_q (None = sentinel), synthesize each via Kokoro,
    concatenate numpy arrays, write one MP3. Returns path or None on failure.
    Synthesis runs in a thread so it overlaps with LLM generation.
    """
    import numpy as np
    import soundfile as sf

    kokoro = await asyncio.to_thread(_get_kokoro)
    arrays = []
    sr = 24000
    # ponytail: kokoro hard-caps at 510 phonemes and throws an index error past that
    # instead of truncating cleanly. sentence_boundary() only splits on terminal
    # punctuation, so an unbroken markdown table/list becomes one oversized chunk and
    # silently loses its audio. Hard-split on whitespace as a safety net; raise this cap
    # if kokoro's phoneme limit changes.
    # 2026-08-01: 300 chars still overflowed 510 phonemes live (e.g. "list my
    # automations" reply, /status reply) — char count isn't phoneme count 1:1,
    # multi-syllable/long-word text runs well over 1 phoneme/char. Dropped to
    # 150 chars, comfortably under the 510-phoneme cap even for dense text.
    _MAX_TTS_CHARS = 150
    while True:
        sentence = await sentence_q.get()
        if sentence is None:
            break
        stripped = _strip_md(sentence)
        words = stripped.split()
        pieces = []
        cur = ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > _MAX_TTS_CHARS:
                pieces.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            pieces.append(cur)
        for piece in pieces:
            try:
                samples, sr = await asyncio.to_thread(
                    kokoro.create, piece, voice=voice, speed=1.0, lang="en-us"
                )
                arrays.append(samples)
                log.debug("TTS chunk synthesized: %d chars", len(piece))
            except Exception as e:
                log.warning("TTS chunk failed: %s", e)

    if not arrays:
        return None
    combined = np.concatenate(arrays)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        await asyncio.to_thread(sf.write, f.name, combined, sr)
        return f.name


async def _send_images_in_text(update: Update, text: str):
    import re
    import os
    # Match absolute file paths ending with png, jpg, jpeg, webp, gif
    img_matches = re.findall(r'(/[^\s:]+\.(?:png|jpe?g|webp|gif))', text)
    seen = set()
    unique_paths = []
    for p in img_matches:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)

    for img_path in unique_paths:
        if os.path.exists(img_path):
            try:
                log.info("Sending photo to Telegram: %s", img_path)
                with open(img_path, "rb") as f:
                    await update.message.reply_photo(photo=f)
            except Exception as e:
                log.warning("Failed to send photo %s: %s", img_path, e)


def _doc_format(query: str) -> tuple[str, str]:
    """Pick output format from the request. Default PDF."""
    q = query.lower()
    if "xlsx" in q or "excel" in q or "spreadsheet" in q or "csv" in q:
        return "Excel spreadsheet", ".xlsx"
    if "docx" in q or "word doc" in q or "word document" in q:
        return "Word document", ".docx"
    if "pptx" in q or "powerpoint" in q or "slides" in q or "presentation" in q:
        return "presentation", ".pptx"
    return "PDF", ".pdf"


def _strip_fences(text: str) -> str:
    """Drop a wrapping ``` / ```lang code fence the model adds despite being told
    not to — a leading '```csv' line would otherwise become a spurious CSV row."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        lines = lines[1:]  # drop the opening ``` / ```lang line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


# Output-format / delivery words that pollute web search toward template-SEO
# pages ("World Cup Excel Spreadsheet template") instead of the actual content.
_FORMAT_NOISE_RE = re.compile(
    r"\b(excel(\s+sheet)?|spreadsheets?|xlsx|csv|word\s+docu\w*|word\s+doc|docx|"
    r"power\s*points?|pptx|slides?|presentations?|pdfs?|documents?)\b",
    re.IGNORECASE,
)


def _content_query(query: str) -> str:
    """Strip output-format words so web search targets the CONTENT, not the file
    format. "today's matches as an excel sheet" → "today's matches" — otherwise
    the rewriter keeps "excel" and the search returns spreadsheet templates."""
    cleaned = re.sub(r"\s{2,}", " ", _FORMAT_NOISE_RE.sub(" ", query)).strip()
    return cleaned or query


def _live_evidence(query: str) -> str:
    """Full reranked web evidence (Tavily + SearXNG snippets) for grounding a
    document. Deliberately bypasses websearch.search()'s temporal fast-path,
    which returns only Tavily's terse ~400-char answer — too thin for a document
    and the reason an earlier run hallucinated a tournament stage. Reuses the
    pipeline's own internal stages (rewrite → search → rerank).
    ponytail: top 8 snippets; raise the cap if documents need more rows."""
    from tools.websearch import _search, _rewrite_query, _rerank
    from tools.search_agentic import _is_temporal
    try:
        search_q = _rewrite_query(_content_query(query))
        res = _search(search_q, time_range="year" if _is_temporal(query) else "")
        if not res.ok or not res.items:
            log.info("[document] no web evidence for %r (search=%r)", query[:60], search_q)
            return ""
        # rerank on the content query (format words would skew relevance)
        ranked = _rerank(_content_query(query), res.items, limit=8)
        evidence = "\n".join(f"- {it.title}: {it.snippet}".strip() for it in ranked if it.snippet)
        log.info("[document] gathered %d chars web evidence (search=%r)", len(evidence), search_q)
        return evidence
    except Exception as e:
        log.warning("[document] evidence fetch failed: %s", e)
        return ""


def _run_doc_agent(query: str, model: str, chat_id: str, on_token):
    """
    Create a document (pdf/docx/pptx/xlsx) deterministically: gather source
    material → ONE LLM synthesis call (Markdown body, or CSV for a spreadsheet)
    → convert → return path.

    Replaces the old gemma4 tool-calling agent loop, which ran ~80s per tool
    round and hung on the long prompts this path builds (see git history +
    CLAUDE.md "gemma4 hangs on long prompts"). The conversion itself is ~150ms;
    the only model work is a single non-agent completion, the same reliable path
    the websearch summarizer uses.

    Returns (confirmation_text, produced_file_path | None).
    """
    from store.conversation import get as conv_get
    from clients.router import _TEMPORAL_RE
    # ponytail: was clients.ollama_client.chat directly — a local-only call with
    # no cloud dispatch. cls.model (the caller) comes from the cloud-first router
    # and is usually a cloud string (e.g. "deepseek/deepseek-v4-flash"), which
    # 404s against local Ollama (confirmed live, repeating in telegram_bot.log).
    # harness.local_chat is the existing is_cloud_model()-dispatching wrapper,
    # same kwarg signature — use it instead of the raw local client.
    from harness import local_chat as llm_chat
    from tools.memory_tools import recall_block
    from tools.document_create import (
        create_pdf, markdown_to_docx, markdown_to_pptx, data_to_xlsx, _write_file,
    )

    # 1. Gather source material. Temporal asks ("today's matches", "latest scores")
    #    need live results; "make a pdf of THIS" refers to earlier turns.
    #    ponytail: last 6 turns, ~6000 chars — widen if users reference older content.
    source = ""
    if _TEMPORAL_RE.search(query):
        evidence = _live_evidence(query)
        if evidence:
            source += (
                "\n\n--- Live web search evidence (Tavily + SearXNG; use ONLY these "
                f"facts) ---\n{evidence}"
            )
    recent = conv_get(chat_id)[-6:]
    if recent:
        transcript = "\n\n".join(
            f"{t.get('role', '?')}: {t.get('content', '')}" for t in recent
        )[-6000:]
        source += f"\n\n--- Recent conversation (source material) ---\n{transcript}"

    # Deliberately still no tool loop here (see docstring) — but it should still be
    # informed by persistent memory (e.g. a remembered preference on formatting/units)
    # rather than starting from zero context every time.
    _mem_block = recall_block(query)
    if _mem_block:
        source = f"\n\n{_mem_block}" + source

    # 2. One synthesis call — write the document body. No tools, no agent loop.
    #    Hard grounding: the model must not invent facts absent from the source —
    #    that's what produced the hallucinated "group stage" in an earlier run.
    #    A spreadsheet needs tabular CSV, not prose — pick the prompt by format.
    fmt, ext = _doc_format(query)
    _grounding = (
        "using ONLY the facts in the source material. Do NOT invent or assume "
        "anything not stated there (no made-up scores, dates, tournament stages, "
        "standings, or names)."
    )
    if ext == ".xlsx":
        prompt = (
            f"Produce the data for a spreadsheet answering the request below, {_grounding} "
            "Include EVERY matching record found in the source, not just one. "
            "Output ONLY CSV: a header row of column names, then one row per record, "
            "comma-separated. No Markdown, no code fences, no commentary. If the source "
            "has no suitable data, output exactly: NO DATA\n\n"
            f"REQUEST: {query}\n{source}"
        )
    else:
        prompt = (
            f"Write the body of a document for the request below, {_grounding} If the "
            "source lacks the requested information, say so plainly instead of guessing. "
            "Output ONLY clean Markdown: a title as a '# ' heading, then sections, bullet "
            "lists, and paragraphs. No preamble, no code fences, no commentary.\n\n"
            f"REQUEST: {query}\n{source}"
        )
    body = _strip_fences(llm_chat(user_message=prompt, model=model, tools=[]) or "")
    if not body or body.strip().upper() == "NO DATA":
        return "Sorry — I couldn't find the data to build that.", None

    # 3. Convert to the requested format (default PDF) and return the artifact path.
    out = os.path.expanduser(f"~/Downloads/clixen_{int(time.time())}{ext}")
    try:
        if ext == ".xlsx":
            csv_path = out[:-5] + ".csv"; _write_file(csv_path, body); path = data_to_xlsx(csv_path, out)
        elif ext == ".docx":
            md = out[:-5] + ".md"; _write_file(md, body); path = markdown_to_docx(md, out)
        elif ext == ".pptx":
            md = out[:-5] + ".md"; _write_file(md, body); path = markdown_to_pptx(md, out)
        else:
            path = create_pdf(body, out)
    except Exception as e:
        log.warning("[document] conversion failed: %s", e)
        return f"Sorry — I wrote the content but couldn't build the {fmt} ({e}).", None

    # Some converters return an error *string* instead of raising (e.g. pptx when
    # python-pptx is missing). Trust the file on disk, not the return value.
    if not (isinstance(path, str) and os.path.exists(path) and os.path.getsize(path) > 0):
        log.warning("[document] converter produced no file for %s: %r", fmt, path)
        return f"Sorry — I couldn't build the {fmt} on this machine.", None

    # Drop the intermediate (.md/.csv the converters write next to the artifact) —
    # the user only wants the final file, not clutter in Downloads.
    for inter in (out[: -len(ext)] + ".md", out[: -len(ext)] + ".csv"):
        try:
            os.path.exists(inter) and os.remove(inter)
        except OSError:
            pass

    confirm = f"Here's your {fmt}."
    if on_token:
        on_token(confirm)
    return confirm, path


# Matches cloud_client._TOOL_PROGRESS_LABELS (e.g. "🔍 Searching the web…") plus the
# "⚙️ Running <tool>…" fallback for tools with no dedicated label — the old pattern only
# covered the fallback, so labels like "🔍 Searching the web…" leaked into TTS audio.
# Reused directly from clients.cloud_client (harness is already imported here, so this
# costs nothing extra) instead of a hardcoded copy — brabble_hook.py hardcodes its own
# copy only because it deliberately avoids importing harness's heavy dependency tree.
from clients.cloud_client import _TOOL_PROGRESS_LABELS as _TG_TOOL_PROGRESS_LABELS
_TOOL_PROGRESS_LABEL_ALT = "|".join(re.escape(l) for l in _TG_TOOL_PROGRESS_LABELS.values())
_TOOL_PROGRESS_TOKEN_RE = re.compile(rf"\n*(?:{_TOOL_PROGRESS_LABEL_ALT}|⚙️ Running [^\n]*…)")

# ponytail: on_token fires for EVERY streamed token across harness.run()'s whole
# multi-round tool loop, including intermediate rounds and cloud_client's
# garbage-recovery nudge retries — not just the final answer. When a round leaks
# malformed content (confirmed live 2026-07-08: a DeepSeek DSML tool-call leak,
# retried twice before falling back to a clean message), all of that garbled
# streamed text still gets voiced before cloud_client even decides it was garbage,
# since the two are decoupled. Result: a runaway Kokoro synthesis storm (~5 min,
# hundreds of tiny chunks, sustained 300-500% CPU) for a reply whose actual
# displayed text was one short fallback sentence. Cap total characters queued for
# synthesis per reply as a blunt but effective backstop — raise if a legitimately
# long spoken answer is getting cut short.
_MAX_TTS_TOTAL_CHARS = 2000


async def _tts_and_send(
    update: Update,
    query: str,
    chat_id: str,
    voice: str = "af_heart",
    stale_label: str = None,
    tts: bool = True,
):
    """
    Stream LLM tokens → sentence buffer → Kokoro synthesis queue → one MP3.
    Text is sent immediately when LLM finishes; audio follows as soon as synthesis completes.
    Pipeline: LLM generation overlaps with per-sentence synthesis → lower total latency.
    When tts=False, no audio is synthesized — text-only reply.
    """
    from chat_ui import _sentence_boundary

    sentence_q: asyncio.Queue = asyncio.Queue()
    synth_task = asyncio.create_task(_synthesize_chunks(sentence_q, voice))

    buf = ""
    tts_chars_sent = 0
    tts_budget_warned = False
    loop = asyncio.get_event_loop()

    def _apply_tts_budget(text: str) -> str:
        # Truncates at the budget boundary rather than an all-or-nothing skip —
        # _sentence_boundary() doesn't guarantee a "ready" flush on every on_token
        # call (garbled/malformed text may lack recognizable sentence punctuation
        # entirely), so unflushed content can pile up in `buf` and arrive in one
        # large chunk at the final flush. An all-or-nothing gate there would let
        # that entire chunk through uncapped — confirmed by test, not just theory.
        # Pure budget accounting only — no queue access, so callers can enqueue
        # via the right mechanism for their thread (see call sites).
        nonlocal tts_chars_sent, tts_budget_warned
        if tts_chars_sent >= _MAX_TTS_TOTAL_CHARS:
            text = ""
        elif tts_chars_sent + len(text) > _MAX_TTS_TOTAL_CHARS:
            text = text[: _MAX_TTS_TOTAL_CHARS - tts_chars_sent]
        if not text and not tts_budget_warned:
            log.warning(
                "TTS synthesis budget (%d chars) exceeded — dropping further "
                "audio for this reply (text reply is unaffected)",
                _MAX_TTS_TOTAL_CHARS,
            )
            tts_budget_warned = True
        tts_chars_sent += len(text)
        return text

    def on_token(token: str):
        if not tts:
            return
        nonlocal buf
        # ollama_client/cloud_client inject "\n\n⚙️ Running <tool>…" progress labels into
        # on_token for the web UI's live-streaming bubble. Telegram has no such bubble —
        # this callback only feeds TTS synthesis — so those labels were getting spoken
        # aloud as their own tiny audio chunks ("Running ask_docs_agent…") between real
        # sentences. Strip them here; the final text reply is unaffected (comes from
        # harness.run()'s return value, not this buffer).
        token = _TOOL_PROGRESS_TOKEN_RE.sub("", token)
        if not token:
            return
        buf += token
        ready, buf = _sentence_boundary(buf)
        ready = _apply_tts_budget(ready)
        if ready:
            # This callback runs off the harness.run() background thread
            # (asyncio.to_thread) — call_soon_threadsafe is required here.
            loop.call_soon_threadsafe(sentence_q.put_nowait, _strip_md(ready))

    doc_path = None
    try:
        # 2026-07-11: dropped the doc-creation-keyword pre-gate — classify_message()
        # already decides intent=="document" semantically (not by keyword match), so
        # the regex pre-gate was redundant duplicate logic. Classify once, uncondition-
        # ally, per message; thread the result through on BOTH branches — previously
        # the non-document else-branch below silently discarded `cls` and made
        # harness.run() re-classify the same text via bare regex from scratch.
        cls = classify_message(query, channel="telegram")
        log.info(
            "[router] q=%r → model=%s intent=%s hint=%s source=%s",
            query[:120], cls.model, cls.intent, cls.specialist_hint, cls.source,
        )
        if cls.intent == "document":
            # Deterministic doc-creation fast path — NOT the LangGraph local-agent
            # (agents/local_agent_graph.py). One synthesis call + file conversion.
            response, doc_path = await asyncio.to_thread(
                _run_doc_agent, query, cls.model, chat_id, on_token,
            )
            model, intent = "doc-fastpath", cls.intent
            # Doc-fastpath bypasses harness.run, so persist history here.
            from store.conversation import append as conv_append
            conv_append(chat_id, "user", query)
            conv_append(chat_id, "assistant", response or "(document created)")
        else:
            response, model, intent = await asyncio.to_thread(
                harness.run_for_messaging,
                query,
                chat_id=chat_id,
                on_token=on_token,
                model=cls.model,
                intent=cls.intent,
                specialist_hint=cls.specialist_hint,
            )
    except Exception:
        sentence_q.put_nowait(None)  # unblock consumer
        try:
            await synth_task
        except Exception:
            pass  # surface the harness error below, not the TTS one
        raise

    # Flush any remaining buffer, then signal synthesis consumer we're done.
    # This runs on the main event-loop thread (not on_token's background thread),
    # so a direct put_nowait is correct — call_soon_threadsafe here would only
    # schedule the put, racing against the very next line's sentinel put and
    # letting it jump the queue (confirmed by test: dropped the flushed chunk).
    flushed = _apply_tts_budget(buf.strip()) if buf.strip() else ""
    if flushed:
        sentence_q.put_nowait(_strip_md(flushed))
    sentence_q.put_nowait(None)

    # Send text immediately — don't wait for audio
    footer = f"\n\n— {model}"
    if stale_label:
        footer += f"  ·  sent {stale_label}"
    await _send_long(update, response + footer)
    await _send_images_in_text(update, response)

    # Deliver the generated document, if the doc agent produced one.
    if doc_path:
        try:
            with open(doc_path, "rb") as f:
                await update.message.reply_document(document=f)
            log.info("[document] sent %s", doc_path)
        except Exception as e:
            log.warning("[document] send failed: %s", e)

    # Wait for synthesis to finish, then send complete audio.
    # TTS is optional — a synthesis/import failure must never break the text reply.
    if tts:
        try:
            mp3_path = await synth_task
        except Exception as e:
            log.warning("TTS synthesis failed: %s", e)
            mp3_path = None
        if mp3_path:
            try:
                with open(mp3_path, "rb") as f:
                    await update.message.reply_audio(audio=f)
                log.info("TTS audio sent (%d chars, model=%s)", len(response), model)
            except Exception as e:
                log.warning("TTS audio send failed: %s", e)
            finally:
                Path(mp3_path).unlink(missing_ok=True)

    return response, model, intent


_STALE_LABEL_SECS = 5 * 60  # > 5 min  → label reply with age
_STALE_SKIP_SECS = 60 * 60  # > 60 min → skip, notify once per burst


def _msg_age_secs(update: Update) -> float:
    """Seconds since the message was sent (Telegram timestamp is UTC-aware)."""
    sent = update.message.date  # already timezone-aware (UTC)
    return (datetime.datetime.now(datetime.timezone.utc) - sent).total_seconds()


_SCREENSHOT_RE = __import__("re").compile(
    r"\b(?:screenshot|screen\s*shot|mac\s*screen|show\s*(?:me\s*)?(?:my\s*)?screen)\b",
    __import__("re").IGNORECASE,
)


# A message that *refers back* to an already-taken screenshot ("use data from the last
# screenshot", "in that screenshot") should NOT retrigger a brand-new live capture — only a
# capture verb governing "screenshot" itself should. Without this, a correction like "you have
# to use data from the last screenshot" gets treated as "take a screenshot" and blindly
# recaptures the current screen instead of falling through to the data the user is pointing at.
# The verb must be within a few words of "screenshot" — checking for the verb ANYWHERE in the
# message is too loose: "...save this in excel and send it to me" contains "send", which has
# nothing to do with capturing a screenshot, and wrongly defeated the reference guard.
_SCREENSHOT_CAPTURE_NEAR_RE = __import__("re").compile(
    r"\b(?:take|capture|grab|get|send|show|make|do)\b(?:\s+\w+){0,3}\s+(?:a\s+|another\s+|my\s+)?screenshot\b",
    __import__("re").IGNORECASE,
)
_SCREENSHOT_REFERENCE_RE = __import__("re").compile(
    r"\b(?:from|in|on|of)\s+(?:the|that|this|my)\b[^.?!]{0,20}\bscreenshot\b"
    r"|\b(?:the|that|this)\s+(?:last|previous|prior|earlier|first|second)?\s*screenshot\b",
    __import__("re").IGNORECASE,
)


def _is_screenshot_request(query: str) -> bool:
    if _SCREENSHOT_REFERENCE_RE.search(query) and not _SCREENSHOT_CAPTURE_NEAR_RE.search(query):
        return False
    return bool(_SCREENSHOT_RE.search(query))


# Written into conversation history only by the real screenshot flow (see conv_append calls
# below) — never something the model should produce on its own.
_SCREENSHOT_OCR_MARKER = "[Raw OCR text from this screenshot"

# Vague continuation phrases ("keep going", "same as usual") carry no "screenshot"/"screen"
# keyword, so _is_screenshot_request never fires for them. Found 2026-07-01: when that
# happened, the message fell through to plain casual chat with no tools — and the model,
# seeing the previous turn end with _SCREENSHOT_OCR_MARKER, fabricated its own copy of that
# bracket with an invented question and a made-up answer, looking exactly like a real capture
# had happened when none had. Recognizing the continuation explicitly routes it through the
# real capture path instead of leaving the model to improvise a fake one.
_SCREENSHOT_CONTINUATION_RE = __import__("re").compile(
    r"^\s*(?:keep going|same as (?:usual|before)|(?:do it |one more time,?\s*)?again|"
    r"next (?:one|question)|continue|one more)\b",
    __import__("re").IGNORECASE,
)


def _is_screenshot_continuation(query: str, chat_id: str) -> bool:
    if not _SCREENSHOT_CONTINUATION_RE.search(query):
        return False
    from store.conversation import get as conv_get
    history = conv_get(chat_id)
    for turn in reversed(history):
        if turn.get("role") == "assistant":
            return _SCREENSHOT_OCR_MARKER in turn.get("content", "")
    return False


# Only send the photo to Telegram when the user explicitly asks for it ("send it to me",
# "show me the screen"). Analyze-only requests ("what's the answer on my screen") skip the photo.
_SEND_PHOTO_RE = __import__("re").compile(
    r"\b(?:send|forward|share)\b|\bshow\s+(?:me\s+)?(?:my\s+|the\s+)?(?:screen|screenshot|image|photo|picture)\b",
    __import__("re").IGNORECASE,
)


def _wants_photo_sent(query: str) -> bool:
    return bool(_SEND_PHOTO_RE.search(query))


# Screenshots are kept here (not deleted immediately) so a bad analysis can be
# debugged against the actual captured image. Swept on each new request instead of
# a cron/background thread — good enough since sweeps only need to happen when the
# dir is being written to anyway.
_SCREENSHOT_CACHE_DIR = Path(__file__).parent / ".screenshot_cache"
_SCREENSHOT_TTL_S = 3600  # 1 hour


def _sweep_screenshot_cache() -> None:
    now = time.time()
    for p in _SCREENSHOT_CACHE_DIR.glob("*.jpg"):
        try:
            if now - p.stat().st_mtime > _SCREENSHOT_TTL_S:
                p.unlink()
        except OSError:
            pass


def _capture_screenshot(screenshot_path: str) -> tuple[bool, str]:
    """Capture the screen, OCR it at native resolution (vision-model reading of dense
    screenshot text was found to hallucinate regardless of resolution — see chat history
    2026-07-01 — while tesseract transcribes it correctly), then save a downscaled JPEG
    at screenshot_path for sending to the user as a photo. Returns (success, ocr_text)."""
    import subprocess
    import os
    png_path = screenshot_path + ".png"
    res = subprocess.run(["/usr/sbin/screencapture", "-x", png_path], capture_output=True)
    if res.returncode != 0:
        return False, ""

    from tools.local_vision import ocr as _ocr_image
    words = _ocr_image(png_path)
    if words and "error" in words[0]:
        log.warning("[screenshot] OCR failed: %s", words[0]["error"])
        ocr_text = ""
    else:
        ocr_text = " ".join(w["text"] for w in words)

    try:
        from PIL import Image
        img = Image.open(png_path)
        native = img.size
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Downscale for the Telegram photo only — OCR above already ran at native res.
        max_size = 800
        w, h = img.size
        if w > max_size or h > max_size:
            if w > h:
                new_w = max_size
                new_h = int(h * (max_size / w))
            else:
                new_h = max_size
                new_w = int(w * (max_size / h))
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:
                resample = Image.LANCZOS
            img = img.resize((new_w, new_h), resample=resample)

        img.save(screenshot_path, "JPEG", quality=80)
        os.remove(png_path)
        kb = os.path.getsize(screenshot_path) // 1024
        log.info("[screenshot] native=%dx%d → sent=%dx%d, %dKB JPEG q80, ocr_chars=%d",
                 native[0], native[1], img.size[0], img.size[1], kb, len(ocr_text))
        return True, ocr_text
    except Exception:
        import shutil
        try:
            shutil.copy(png_path, screenshot_path)
            os.remove(png_path)
        except Exception:
            pass
        return True, ocr_text


def _analyze_screenshot_ocr(query: str, ocr_text: str, on_token=None) -> str | None:
    try:
        import ollama
        if not ocr_text.strip():
            return "Couldn't read any text on the screen clearly — try again or move the window."
        prompt = (
            f"Context: The system has already successfully captured and shared the user's Mac screen. Do NOT refuse or state that you cannot see the screen or make a screenshot. Simply analyze the text captured from their screen to answer their request.\n\n"
            f"Text OCR'd from the user's Mac screenshot (may include unrelated UI clutter like "
            f"menu bars or app names — ignore those):\n\n{ocr_text}\n\n"
            f"User question: {query}\n\n"
            f"Rules: Answer using only the text above — never say you cannot see the screen or cannot take a screenshot. "
            f"Reply in 1-3 short sentences, no headers, no bullet lists, no preamble. "
            f"For a multiple-choice question (medical, English reading comprehension, grammar, or any "
            f"subject), give the correct option and a one-line reason grounded only in the text shown."
        )
        t0 = time.time()
        full = ""
        eval_n = 0
        for chunk in ollama.chat(
            model="gemma4:12b-mlx",
            messages=[{"role": "user", "content": prompt}],
            keep_alive=-1,
            options={"temperature": 0.1, "num_predict": 220},
            think=False,
            stream=True,
        ):
            tok = chunk.message.content or ""
            if tok:
                full += tok
                if on_token:
                    on_token(tok)
            if getattr(chunk, "eval_count", None):
                eval_n = chunk.eval_count
        elapsed = time.time() - t0
        tok_s = (eval_n / elapsed) if elapsed else 0
        log.info("[screenshot] gemma4:12b-mlx ocr-reason: %.1fs, %d tokens, %.1f tok/s", elapsed, eval_n, tok_s)
        log.info("[screenshot] answer: %s", full.strip().replace("\n", " ")[:2000])
        return full
    except Exception as e:
        log.warning("OCR-reason path failed: %s", e)
        return None


def _age_label(secs: float) -> str:
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    return f"{int(secs // 3600)}h {int((secs % 3600) // 60)}m ago"


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        log.warning("Blocked user %s", update.effective_user.id)
        return

    photo = update.message.photo[-1]
    caption = (update.message.caption or "").strip()
    query = caption or "Read and analyze all visible text in this image. If it's a test question, give the correct answer with a one-line reason."
    log.info("photo from %s (%dpx, caption=%r)", update.effective_user.id, photo.width, caption[:80])

    chat_id = str(update.effective_chat.id)
    await update.message.chat.send_action("typing")

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        photo_path = tmp.name

    try:
        tg_file = await _tg_retry(lambda: photo.get_file(), "photo.get_file")
        await _tg_retry(lambda: tg_file.download_to_drive(photo_path), "photo.download")
        log.info("[photo] downloaded to %s", photo_path)

        placeholder = await update.message.reply_text("Analyzing image…")
        loop = asyncio.get_event_loop()
        q: asyncio.Queue = asyncio.Queue()

        def on_token(tok: str):
            loop.call_soon_threadsafe(q.put_nowait, tok)

        analysis_task = asyncio.create_task(
            asyncio.to_thread(_analyze_screenshot_ocr, query, photo_path, on_token)
        )

        shown = ""
        last_edit = 0.0
        while not analysis_task.done() or not q.empty():
            try:
                shown += await asyncio.wait_for(q.get(), timeout=0.4)
            except asyncio.TimeoutError:
                pass
            if shown and time.time() - last_edit > 1.5:
                try:
                    await placeholder.edit_text(_strip_md(shown)[:4096])
                    last_edit = time.time()
                except Exception:
                    pass

        analysis = await analysis_task
        final = _strip_md(analysis)[:4096] if analysis else "Could not analyze the image."
        try:
            await placeholder.edit_text(final)
        except Exception:
            pass
        if analysis:
            from store.conversation import append as conv_append
            conv_append(chat_id, "user", query)
            conv_append(chat_id, "assistant", analysis)
    finally:
        Path(photo_path).unlink(missing_ok=True)


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        log.warning("Blocked user %s", update.effective_user.id)
        return

    query = update.message.text.strip()
    age = _msg_age_secs(update)
    log.info("text from %s (age %.0fs): %s", update.effective_user.id, age, query[:80])

    if age > _STALE_SKIP_SECS:
        log.info("skipping stale message (%.0f min old): %s", age / 60, query[:60])
        await update.message.reply_text(
            f"(I was offline when you sent this {_age_label(age)} — skipping it. "
            f"Send it again if you still need an answer.)"
        )
        return

    chat_id = str(update.effective_chat.id)
    await update.message.chat.send_action("typing")
    await _dispatch_query(update, query, chat_id, age)


async def _dispatch_query(update: Update, query: str, chat_id: str, age: float) -> None:
    """Shared fast-path dispatch (screenshot / email / inbox / generic chat) for a query,
    regardless of whether it arrived as typed text or a transcribed voice note. Voice used to
    skip this entirely and go straight to the generic chat pipeline, so a spoken "take a
    screenshot and send it to me" never actually took a screenshot — it just got classified
    as a normal chat/OCR request instead."""
    try:
        # Quick screenshot path
        if _is_screenshot_request(query) or _is_screenshot_continuation(query, chat_id):
            _ss_t0 = time.time()
            log.info("[screenshot] request: %r", query[:120])
            _SCREENSHOT_CACHE_DIR.mkdir(exist_ok=True)
            _sweep_screenshot_cache()
            screenshot_path = str(_SCREENSHOT_CACHE_DIR / f"{int(_ss_t0)}_{chat_id}.jpg")
            try:
                success, ocr_text = await asyncio.to_thread(_capture_screenshot, screenshot_path)
                if not success:
                    await update.message.reply_text("Failed to capture Mac screenshot.")
                    return

                # Send photo only if the user explicitly asked for it; analyze-only requests skip it.
                # Photo upload (when sent) overlaps gemma; tokens appear ~10s in instead of ~22s at once.
                send_photo = _wants_photo_sent(query)
                log.info("[screenshot] streaming ocr-reason analysis (send_photo=%s)...", send_photo)
                photo_task = None
                if send_photo:
                    with open(screenshot_path, "rb") as photo_file:
                        img_bytes = photo_file.read()
                    photo_task = asyncio.create_task(update.message.reply_photo(photo=img_bytes))
                placeholder = await update.message.reply_text("Analyzing screen…")

                loop = asyncio.get_event_loop()
                q: asyncio.Queue = asyncio.Queue()
                def on_token(tok: str):
                    loop.call_soon_threadsafe(q.put_nowait, tok)

                analysis_task = asyncio.create_task(
                    asyncio.to_thread(_analyze_screenshot_ocr, query, ocr_text, on_token)
                )

                shown = ""
                last_edit = 0.0
                # Drain tokens, edit the placeholder at most every 1.5s (Telegram flood limit)
                while not analysis_task.done() or not q.empty():
                    try:
                        shown += await asyncio.wait_for(q.get(), timeout=0.4)
                    except asyncio.TimeoutError:
                        pass
                    if shown and time.time() - last_edit > 1.5:
                        try:
                            await placeholder.edit_text(_strip_md(shown)[:4096])
                            last_edit = time.time()
                        except Exception:
                            pass  # flood / "not modified" — skip this tick

                analysis = await analysis_task
                if photo_task is not None:
                    await photo_task
                final = _strip_md(analysis)[:4096] if analysis else "Could not analyze the screenshot."
                try:
                    await placeholder.edit_text(final)
                except Exception:
                    pass  # final text identical to last stream edit
                # Write the screen Q&A into the shared conversation store so follow-ups
                # ("why not option C?", "save this in excel") routed through harness.run have
                # this context. The spoken answer alone is a lossy 1-3 sentence summary (by
                # design, for readability) — it doesn't carry the real numbers/table a later
                # "export this" request needs, so the raw OCR text is tacked on for the model
                # to see, without ever being shown to the user (Telegram only gets `final`).
                if analysis:
                    from store.conversation import append as conv_append
                    conv_append(chat_id, "user", query)
                    stored = analysis
                    if ocr_text.strip():
                        stored += f"\n\n{_SCREENSHOT_OCR_MARKER}, for later reference: {ocr_text[:2000]}]"
                    conv_append(chat_id, "assistant", stored)
                log.info("[screenshot] done, total %.1fs", time.time() - _ss_t0)
                return
            finally:
                pass  # kept for debugging — _sweep_screenshot_cache() expires it after _SCREENSHOT_TTL_S

        # Email queries (inbox, sender, search) used to be intercepted here by
        # keyword-anywhere regexes (_INBOX_RE et al.) before harness.run() ever saw
        # the message — greedy-capturing everything after the match to end-of-string
        # and silently dropping the rest of a multi-intent message (e.g. "create a
        # folder ... and check my inbox for attachments" would hijack on "inbox" and
        # discard the folder-creation half). Removed 2026-07: ask_email_agent (routed
        # through harness.run()'s orchestrator, same as every other subagent) already
        # reasons about the actual query instead of pattern-matching a keyword
        # anywhere in it — let it handle inbox/sender/search email requests directly.
        label = _age_label(age) if age > _STALE_LABEL_SECS else None
        _mark_inflight(chat_id, query)
        response, model, intent = await _tts_and_send(
            update, query, chat_id, stale_label=label,
            tts=_tts_enabled.get(chat_id, False),
        )
        log.info("routed to %s (%s), response len=%d", model, intent, len(response))
    except Exception as e:
        log.error("harness.run failed: %s", e, exc_info=True)
        await update.message.reply_text(f"Error: {e}")
    finally:
        _clear_inflight()


def _transcribe_local(audio_path: str) -> str:
    """Transcribe using the cached faster-whisper model (lazy-loaded on first call)."""
    segments, _ = _get_whisper().transcribe(audio_path, beam_size=1, language=None)
    return " ".join(s.text.strip() for s in segments).strip()


def _store_in_kb(transcript: str, audio_path: str):
    """KB store — runs in dedicated background thread, never blocks the reply."""
    try:
        from store.knowledge_base import KnowledgeBase

        KnowledgeBase().store(
            transcript,
            source="audio_transcript",
            query=Path(audio_path).name,
            url=audio_path,
            auto_chunk=True,
        )
        log.info("KB store complete for %s", Path(audio_path).name)
    except Exception as e:
        log.warning("KB store failed: %s", e)


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Download voice note → faster-whisper (cached) → LLM → reply. KB store is fire-and-forget."""
    if not _allowed(update):
        return

    age = _msg_age_secs(update)
    if age > _STALE_SKIP_SECS:
        log.info("skipping stale voice (%.0f min old)", age / 60)
        await update.message.reply_text(
            f"(I was offline when you sent this voice note {_age_label(age)} — skipping it. "
            f"Send it again if you still need an answer.)"
        )
        return

    await update.message.chat.send_action("typing")

    voice = update.message.voice or update.message.audio
    tg_file = await _tg_retry(lambda: voice.get_file(), "voice.get_file")

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await _tg_retry(lambda: tg_file.download_to_drive(tmp.name), "voice.download")
        audio_path = tmp.name

    log.info("voice from %s → %s", update.effective_user.id, audio_path)

    try:
        transcript = await asyncio.to_thread(_transcribe_local, audio_path)
        log.info("transcript: %s", transcript[:120])
    except Exception as e:
        log.error("transcription failed: %s", e, exc_info=True)
        await update.message.reply_text(f"Transcription error: {e}")
        Path(audio_path).unlink(missing_ok=True)
        return

    # KB store is fire-and-forget — dedicated thread pool, never blocks reply
    _kb_executor.submit(_store_in_kb, transcript, audio_path)
    Path(audio_path).unlink(missing_ok=True)

    chat_id = str(update.effective_chat.id)
    await update.message.chat.send_action("typing")
    # Route through the same fast-path dispatch as typed text (screenshot/email/inbox) instead
    # of going straight to the generic chat pipeline — see _dispatch_query's docstring.
    await _dispatch_query(update, transcript, chat_id, age)


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Clear this chat's conversation history and start fresh."""
    if not _allowed(update):
        return
    from store.conversation import clear as conv_clear
    conv_clear(str(update.effective_chat.id))
    await update.message.reply_text("Started a new conversation.")


async def cmd_tts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Toggle TTS voice responses for this chat (default off) — /tts alone
    flips the toggle; /tts <question> always synthesises regardless of toggle."""
    if not _allowed(update):
        return

    query = " ".join(ctx.args).strip()
    chat_id = str(update.effective_chat.id)

    if not query:
        current = _tts_enabled.get(chat_id, False)
        new_state = not current
        _tts_enabled[chat_id] = new_state
        await update.message.reply_text(f"TTS {'ON' if new_state else 'OFF'}")
        return

    await update.message.chat.send_action("typing")
    try:
        _mark_inflight(chat_id, query)
        await _tts_and_send(update, query, chat_id)
    finally:
        _clear_inflight()


# ---------------------------------------------------------------------------
# Agent command
# ---------------------------------------------------------------------------


async def cmd_agent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /agent <task>  — run a multi-step autonomous agent.

    Sends a live progress message that updates as the agent works through rounds,
    then replies with the final result.
    """
    if not _allowed(update):
        return

    task = " ".join(ctx.args).strip()
    if not task:
        await update.message.reply_text(
            "Usage: /agent <task>\n\n"
            "Examples:\n"
            "  /agent find all Python files in ~/Developer and count lines of code\n"
            "  /agent create a script that backs up my .env files\n"
            "  /agent check which processes are using the most CPU right now"
        )
        return

    log.info("agent task from %s: %s", update.effective_user.id, task[:120])

    # Send a live status message we'll edit as progress comes in
    status_msg = await update.message.reply_text("Agent starting...")
    progress_lines = []

    async def _edit_status(text: str):
        """Update the status bubble — debounced to avoid Telegram rate limits."""
        try:
            await status_msg.edit_text(text[:4000])
        except Exception:
            pass  # ignore edit race conditions

    def on_progress(line: str):
        """Called from the background thread — schedules a status edit."""
        progress_lines.append(line)
        # Show last 8 lines of progress
        summary = "\n".join(progress_lines[-8:])
        asyncio.run_coroutine_threadsafe(
            _edit_status(f"Agent working...\n\n{summary}"),
            asyncio.get_event_loop(),
        )

    try:
        chat_id = str(update.effective_chat.id)
        result, _, _ = await asyncio.to_thread(harness.run_for_messaging, task, on_token=None, chat_id=chat_id)
        # Replace status message with final result
        await status_msg.edit_text(f"Agent done.\n\n{result[:4000]}")
        await _send_images_in_text(update, result)
        # If result is long, send remainder as follow-ups
        if len(result) > 4000:
            for i in range(4000, len(result), 4096):
                await update.message.reply_text(result[i : i + 4096])
    except Exception as e:
        log.error("agent_run failed: %s", e, exc_info=True)
        await status_msg.edit_text(f"Agent error: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    """Catch-all: log and notify the user on any unhandled exception."""
    log.error("Unhandled exception", exc_info=ctx.error)
    if isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text(f"Unexpected error: {ctx.error}")
        except Exception:
            pass


async def _post_shutdown(app):
    """Cancel the internet-monitor background task before the event loop closes.
    Previously fire-and-forget (asyncio.create_task with no stored handle) — its
    infinite `while True` loop was still mid-`sleep()` whenever the process exited
    or restarted, producing a harmless but noisy 'Task was destroyed but it is
    pending!' on every restart (confirmed live: all 8 occurrences in
    telegram_bot.log correlate exactly with a restart, always this same task)."""
    task = app.bot_data.get("internet_monitor_task")
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _post_init(app):
    """Start APScheduler + internet monitor once the event loop is running."""
    app.bot_data["internet_monitor_task"] = asyncio.create_task(InternetMonitor().run())
    stale = _inflight.pop_stale("telegram")
    if stale:
        try:
            await app.bot.send_message(
                chat_id=stale["chat_id"],
                text=f"(I crashed while answering \"{stale['query'][:200]}\" — please resend it.)",
            )
        except Exception as e:
            log.warning("failed to send crash-recovery notice: %s", e)

    log.info("Starting APScheduler (reminders)...")
    _scheduler.start()
    log.info("Bot polling — waiting for messages...")


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")

    # Optional base URL override — Cloudflare Worker reverse proxy (TELEGRAM_BASE_URL in .env)
    # When set, use standard TLS (Cloudflare has valid certs). Otherwise use permissive SSL
    # to survive Cisco Umbrella TLS interception on direct connections to api.telegram.org.
    base_url = os.environ.get("TELEGRAM_BASE_URL", "").strip()

    if base_url:
        # Cloudflare Worker: standard TLS, no proxy needed
        httpx_kwargs: dict = {}
        log.info("Telegram base URL overridden to %s", base_url)
    else:
        # Direct connection through Umbrella — permissive SSL required
        import ssl

        _ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        _ssl_ctx.check_hostname = False
        _ssl_ctx.verify_mode = ssl.CERT_NONE
        _ssl_ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        _ssl_ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
        httpx_kwargs: dict = {"verify": _ssl_ctx}

        # Optional SOCKS5/HTTP proxy override (TELEGRAM_PROXY_URL in .env)
        proxy_url = os.environ.get("TELEGRAM_PROXY_URL", "").strip()
        if proxy_url:
            httpx_kwargs = {"verify": False, "proxy": proxy_url}
            log.info("Telegram requests tunnelled via %s", proxy_url)

    # Default read_timeout (5s) is tight for the response after a photo upload — PTB already
    # gives media_write_timeout=20s for the upload itself, so match that on the read side.
    request = HTTPXRequest(http_version="1.1", read_timeout=20, httpx_kwargs=httpx_kwargs)

    builder = Application.builder().token(TOKEN).request(request).post_init(_post_init).post_shutdown(_post_shutdown)
    if base_url:
        builder = builder.base_url(base_url)
    app = builder.build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("usage", cmd_usage))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("tts", cmd_tts))
    app.add_handler(CommandHandler("agent", cmd_agent))
    app.add_handler(CommandHandler("tutor", cmd_tutor))
    app.add_handler(CommandHandler("learn", cmd_learn))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    global _bot_app_ref
    _bot_app_ref = app

    log.info("Warming classifier...")
    warm_classifier()
    # ponytail: Kokoro loaded on first TTS request (lazy), not at startup.
    # Saves ~2GB RAM and avoids OOM kill loop on memory-pressure machines.
    # timeout=20 keeps long-poll under Cloudflare Worker's 30s subrequest limit
    # Default signal handling: SIGTERM/SIGINT → graceful updater stop → clean
    # poll disconnect → no "Conflict" on restart (found 2026-07-09, the
    # previous stop_signals=() let SIGTERM kill mid-poll, leaving orphaned
    # connections that caused "terminated by other getUpdates request").
    app.run_polling(allowed_updates=Update.ALL_TYPES, timeout=20)


if __name__ == "__main__":
    main()
