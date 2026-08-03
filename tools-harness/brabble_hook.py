#!/usr/bin/env python3
"""Brabble wake-word hook.

Brabble (https://github.com/steipete/brabble) fires a configured hook each time
the wake word is heard. The transcript is passed in BRABBLE_TEXT.

Features:
- Lock file prevents concurrent harness invocations — rejects with "busy" if already running
- Sentence-boundary TTS truncation, detached from hook process group

To enable, add to ~/.config/brabble/config.toml:

    [hook]
    command = "/Users/kalinovdameus/Developer/clixen/tools-harness/brabble_hook.py"
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
# src.g4l directory has been removed. Pydantic models moved to tools-harness/chat_ui.py

# cloud_client.py injects "\\n\\n<label>" progress labels (from a fixed, known
# set — _TOOL_PROGRESS_LABELS) into on_token for the web UI's live-streaming
# bubble, e.g. "🔍 Searching the web…". Brabble has no bubble, only TTS, so
# these were getting spoken aloud between real sentences (confirmed live in
# brabble.log: mic picked up "Magnifying glass, searching the web…" mid-reply).
# Match only the exact known labels — matching on emoji alone is too broad and
# ate real content that happens to use the same emoji (e.g. "☁️ Condition:" in
# a weather answer). ponytail: hardcoded copy, not imported from cloud_client —
# `import clients.cloud_client` alone costs ~2.4s (openai SDK, dotenv, tool
# registry) just to reach a 12-entry dict, defeating this file's whole point
# of avoiding cold-import latency. Keep in sync with cloud_client.py's
# _TOOL_PROGRESS_LABELS if that dict changes.
_KNOWN_LABELS = (
    "🔍 Searching the web…", "🌐 Opening browser…", "📂 Working on files…",
    "📄 Reading file…", "✏️ Writing file…", "🗑️ Deleting…", "✂️ Renaming…",
    "⚙️ Running command…", "🍎 macOS action…", "📧 Checking email…",
    "✅ Managing tasks…", "📅 Checking calendar…",
)
# \n* (not \n\n) — cloud_client.py/ollama_client.py always inject with a "\n\n"
# prefix, but chat_ui.py's streaming token-forwarding path (preamble/think
# filters) doesn't always preserve it verbatim before it reaches this hook —
# confirmed live: a weather query arrived as one glued blob, label directly
# concatenated onto the full answer with no separator at all. Match with or
# without leading newlines so it strips either way.
_LABEL_ALT = "|".join(re.escape(l) for l in _KNOWN_LABELS)
_TOOL_PROGRESS_RE = re.compile(rf"\n*(?:{_LABEL_ALT}|⚙️ Running [^\n]*…)")

def _log_lat(label: str, t0: float) -> None:
    """e2e latency checkpoint, wake-word-heard (t0) to `label`. ponytail:
    stderr write matching this file's existing log style, no logging
    framework — brabble.log already captures stderr."""
    sys.stderr.write(f"[brabble_lat] {label}: {time.perf_counter() - t0:.3f}s\n")


CHAT_ID = "brabble_voice"
VOICEPRINT_PATH = os.path.expanduser("~/.config/g4l/voiceprint/voiceprint.npy")
VERIFY_THRESHOLD = 0.55

LOCK_FILE = "/tmp/brabble_hook.lock"
PENDING_CONFIRM_FILE = "/tmp/brabble_pending_confirm.json"
CONFIRM_POLL_SEC = 1.5

_YES_RE = re.compile(r"\b(yes|yeah|yep|yup|confirm|approve|go ahead|do it)\b", re.I)
_NO_RE = re.compile(r"\b(no|nope|nah|cancel|deny|don'?t|stop)\b", re.I)

MAX_TTS_CHARS = 500

# Grounded queries (sports/temporal → forced subagent lookup) take ~5.4s to
# first token vs ~2s for a direct answer (measured, see brabble_lat logs): the
# harness forces a serial round0→subagent→round1 chain. Mask that dead air with
# a short filler spoken only if no token has arrived by _FILLER_AFTER_S — direct
# answers arrive first and cancel it, so fast queries stay filler-free.
_FILLER_AFTER_S = 1.2
_FILLERS = ("Let me check.", "One sec.", "Looking that up.")
# Serializes filler playback against the real reply's audio so they never
# overlap — filler plays out-of-band (during the wait), the pipeline drains
# under the same lock. ponytail: one lock, not per-utterance coordination.
_PLAYBACK_LOCK = threading.Lock()

# macOS `say` reads emoji aloud by their full Unicode name ("smiling face with
# smiling eyes and rosy cheeks…") instead of skipping them — content emoji in
# real answers (🌡️☁️💰 etc, deliberately preserved for print/UI, see
# _TOOL_PROGRESS_RE above) still need to be dropped specifically at the TTS
# boundary. Broad ranges covering pictographs/emoticons/symbols/flags plus the
# variation-selector and zero-width-joiner used to build compound emoji.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\uFE0F"
    "\u200D"
    "]+"
)

# Same markdown-stripping pattern as chat_ui.py's/telegram_bot.py's _strip_md \u2014
# without it, both `say` and Kokoro speak literal asterisks/hashes/backticks
# ("asterisk asterisk 3:15 PM asterisk asterisk" for "**3:15 PM**", confirmed
# live). ponytail: hardcoded copy, not imported, same reason as _EMOJI_RE above
# \u2014 importing chat_ui.py here would drag in its whole FastAPI app module just
# for one regex, defeating this file's cold-exec-fast design.
_MD_STRIP_RE = re.compile(r"(\*{1,3}|_{1,3}|`{1,3}|~~|#{1,6}\s?)")

# 2026-07-11: markdown tables reach here from the search-graph path
# (tools/websearch.py's summarizer has no voice/channel awareness — it always
# produces the same table/heading-formatted answer used by the web UI) and
# _MD_STRIP_RE above doesn't touch table pipes or horizontal rules, so Kokoro
# read "| Item | Value |" and "---" literally (confirmed live: a gold-price
# table made TTS drain take 81s vs ~25s for an equivalent plain-prose reply).
# Strip pipe characters and 3+-run dash/equals horizontal rules; leave single
# "-" alone since that's also used as a minus sign / hyphen in normal prose.
_TABLE_PIPE_RE = re.compile(r"\|")
_HR_RULE_RE = re.compile(r"[-=]{3,}")

# 2026-07-10: was `from chat_ui import sentence_boundary` inside
# _run_harness_streaming() — importing the entire FastAPI app (routes, DB
# inits, tool registry) just for this 15-line stdlib-only function cost
# 2.50s measured, on top of the ~4.5s this file already dodges by not
# importing harness.py. ponytail: hardcoded copy, identical to chat_ui.py's
# sentence_boundary() — same reasoning as _EMOJI_RE/_MD_STRIP_RE above.
_SENTENCE_BOUNDARY_RE = re.compile(r"([.!?…])\s+(?=[A-Z])")


def sentence_boundary(buf: str) -> tuple[str, str]:
    if not buf:
        return "", ""
    for marker in (".\n", "!\n", "?\n", "\n\n"):
        idx = buf.find(marker)
        if idx != -1:
            chunk = buf[: idx + len(marker)].strip()
            rest = buf[idx + len(marker):]
            if len(chunk.split()) >= 3:
                return chunk, rest
    m = _SENTENCE_BOUNDARY_RE.search(buf)
    if m and len(buf[: m.end()].split()) >= 4:
        return buf[: m.end()].strip(), buf[m.end():]
    return "", buf


def _clean_for_tts(text: str) -> str:
    """Strip markdown/emoji and truncate at a sentence boundary within
    MAX_TTS_CHARS. Shared by _speak() and the pipelined synthesizer below.

    Callers pass both short single-line utterances (fillers, confirmation
    prompts — never contain '\\n') and full multi-paragraph replies (the
    _run_harness_blocking() fallback in _run_harness(), used whenever
    streaming fails). Taking only the first '\\n'-delimited line used to
    silently drop the entire answer whenever it happened to be more than one
    line (found 2026-07-10: "Sure!\\n\\nHere's the forecast: ..." spoke only
    "Sure!"). Collapse newlines to spaces instead so multi-paragraph content
    survives — MAX_TTS_CHARS below still caps total length."""
    clean = " ".join(text.strip().split())
    if not clean:
        return ""
    clean = _MD_STRIP_RE.sub("", clean)
    clean = _HR_RULE_RE.sub(" ", clean)
    clean = _TABLE_PIPE_RE.sub(" ", clean)
    clean = " ".join(clean.split())
    clean = _EMOJI_RE.sub("", clean).strip()
    if not clean:
        return ""
    if len(clean) > MAX_TTS_CHARS:
        truncated = clean[:MAX_TTS_CHARS]
        last_boundary = max(
            truncated.rfind(". "), truncated.rfind("! "),
            truncated.rfind("? "), truncated.rfind("\n"),
        )
        if last_boundary > MAX_TTS_CHARS * 0.5:
            clean = clean[: last_boundary + 1]
        else:
            clean = truncated
    elif len(clean) > 150 and clean[-1] not in ".!?\"'’":
        # 2026-07-11: a server-side abort (brabble_hook.py's own 45s/60s
        # _abort_timer, or a genuine barge-in) cuts the reply off wherever
        # generation happened to be — mid-word sometimes (confirmed live:
        # "...And if your ac") — and that raw fragment used to get spoken
        # verbatim. Only trim when reasonably long and missing terminal
        # punctuation (a short unpunctuated reply, e.g. a bare number, is
        # more likely complete-but-unpunctuated than actually cut off) and
        # only when a real sentence boundary exists to fall back to —
        # otherwise leave it whole, since speaking something beats nothing.
        last_boundary = max(clean.rfind(". "), clean.rfind("! "), clean.rfind("? "))
        if last_boundary > len(clean) * 0.3:
            clean = clean[: last_boundary + 1]
    return clean


# ponytail: kokoro hard-caps at 510 phonemes and throws an IndexError past
# that instead of truncating cleanly (confirmed live via recurring
# task_worker.log/core_stderr.log crashes). _clean_for_tts()'s sentence-aware
# truncation doesn't prevent this — an unpunctuated run (table, list, dense
# numeric text) can still produce one oversized chunk, and chars don't map
# 1:1 to phonemes anyway. telegram_bot.py's _synthesize_chunks already hit
# and fixed this with a hard word-wrap fallback (see its own ponytail
# comment) — mirrored here rather than importing telegram_bot.py itself
# (same "avoid a heavy import" reason sentence_boundary() is duplicated
# above). Raise this cap if kokoro's phoneme limit changes.
_KOKORO_MAX_CHARS = 300


def _wrap_for_kokoro(clean: str, max_chars: int = _KOKORO_MAX_CHARS) -> list[str]:
    """Hard word-wrap so no single piece handed to Kokoro can exceed max_chars,
    regardless of internal punctuation."""
    words = clean.split()
    pieces: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            pieces.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        pieces.append(cur)
    return pieces


def _fallback_speak(text: str) -> None:
    """Last-resort TTS when Kokoro is down: macOS `say`, else nothing (log only).

    On Linux `say` doesn't exist and would silently fail — keep the call from
    crashing and surface a warning instead of fake audio."""
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["say", "-r", "180", text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            return
        except Exception:
            pass
    import logging
    logging.getLogger("brabble_hook").warning(
        "no TTS available on this platform (Kokoro down, macOS `say` missing) — audio skipped"
    )


def _speak(text: str) -> None:
    """Speak text via TTS, blocking until playback finishes.

    Must block: the busy-lock is held by the caller only while this runs.
    If playback were fire-and-forget, the lock would release mid-playback and
    the mic would hear our own reply, treat it as a new command, and
    re-dispatch it into the harness — a self-triggering feedback loop.

    Used for one-off utterances (confirmation prompts, non-streaming
    fallback). The main streaming reply path uses _TTSPipeline instead, which
    overlaps synthesis of the next sentence with playback of the current one.
    """
    clean = _clean_for_tts(text)
    if not clean:
        return
    played = False
    player = _StreamPlayer()
    try:
        for piece in _wrap_for_kokoro(clean):
            for wav in _synthesize_kokoro_chunks(piece):
                player.play(wav)
                played = True
    finally:
        player.close()
    if played:
        return
    _fallback_speak(clean)


def _read_exact(resp, n: int) -> bytes:
    """Read exactly n bytes from a streaming HTTP response, or b'' at EOF."""
    buf = b""
    while len(buf) < n:
        piece = resp.read(n - len(buf))
        if not piece:
            return b""
        buf += piece
    return buf


def _synthesize_kokoro_chunks(text: str):
    """Yield WAV byte-chunks from the warm Kokoro daemon's streaming endpoint
    (af_heart — Kokoro's own highest-graded English voice) as they're
    generated. Yields nothing on any failure (daemon down, timeout) so the
    caller always has a clean signal to fall back to `say`.
    """
    import json
    import struct
    import urllib.request as _req

    try:
        payload = json.dumps({"text": text, "voice": "af_heart"}).encode()
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("KOKORO_AUTH_TOKEN", "").strip()
        if token:
            headers["X-Auth-Token"] = token
        request = _req.Request(
            "http://127.0.0.1:9237/synthesize_stream", data=payload,
            headers=headers, method="POST",
        )
        resp = _req.urlopen(request, timeout=15)
    except Exception:
        return

    try:
        while True:
            header = _read_exact(resp, 4)
            if not header:
                break
            (chunk_len,) = struct.unpack(">I", header)
            wav_bytes = _read_exact(resp, chunk_len)
            if not wav_bytes:
                break
            yield wav_bytes
    except Exception:
        return
    finally:
        try:
            resp.close()
        except Exception:
            pass


# 2026-07-10: brabble has no AEC and no mic-mute/duck API (confirmed via
# `brabble --help`/`doctor` — nothing exposed for this, and it's a closed Go
# binary we can't patch). The mic stays live through our own TTS output, so
# a real wake-word attempt spoken while we're talking can get acoustically
# masked by our own voice in the same mic feed and never get recognized —
# confirmed live: every reply produces a "heard: <our own words>" line with
# no wake-word match, proving the mic clearly picks up our output. Lowering
# playback volume is a mitigation (less energy to mask a real wake word
# over), not a fix — true AEC would need reference-signal cancellation we
# have no hook to add. afplay supports per-call -v; `say` (fallback only)
# does not, so this only covers the primary Kokoro path.
TTS_VOLUME = "0.6"


class _StreamPlayer:
    """Feeds raw PCM into one long-lived `sox` process instead of spawning
    afplay per chunk. Each afplay call opens a fresh CoreAudio stream; over
    Bluetooth (A2DP) that forces a route re-negotiate/buffer-fill (~100-300ms)
    between sentences, heard as a cut/stutter — inaudible on wired/built-in
    output where stream-open is near-instant. Keeping one process/stream open
    across an utterance avoids the re-negotiate entirely."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._rate: int | None = None

    def _ensure_proc(self, rate: int, channels: int, sampwidth: int) -> None:
        if self._proc is not None and self._rate == rate:
            return
        self.close()
        self._rate = rate
        self._proc = subprocess.Popen(
            ["sox", "-q", "-t", "raw", "-r", str(rate), "-e", "signed",
             "-b", str(sampwidth * 8), "-c", str(channels), "-",
             "-t", "coreaudio", "default", "vol", TTS_VOLUME],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def play(self, wav_bytes: bytes) -> None:
        import io
        import wave

        try:
            with wave.open(io.BytesIO(wav_bytes)) as w:
                rate, channels, sampwidth = w.getframerate(), w.getnchannels(), w.getsampwidth()
                frames = w.readframes(w.getnframes())
            self._ensure_proc(rate, channels, sampwidth)
            self._proc.stdin.write(frames)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._proc = None
        except Exception:
            pass

    def close(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


class _TTSPipeline:
    """Overlaps sentence synthesis with playback: a background thread
    synthesizes sentence N+1 (network round-trip to the Kokoro daemon, the
    ~0.5-1.5s part) WHILE the main thread plays sentence N's audio (afplay,
    blocking for real-time playback duration). Without this, each sentence
    after the first pays its own synthesis time as dead air between
    sentences — the synthesizer only starts sentence N+1 once sentence N's
    *playback* has already finished. Falls back to `say` per-sentence if
    Kokoro synthesis yields nothing for that sentence.

    Must still fully drain (join()) before the caller releases LOCK_FILE —
    same self-triggering-feedback-loop constraint as _speak().
    """

    def __init__(self, t0: float | None = None):
        self._sentence_q: "queue.Queue[str | None]" = queue.Queue()
        self._audio_q: "queue.Queue[tuple[str, object] | None]" = queue.Queue()
        self._t0 = t0
        self._logged_first_audio = False
        self._worker = threading.Thread(target=self._synthesize_loop, daemon=True)
        self._worker.start()

    def _synthesize_loop(self) -> None:
        while True:
            sentence = self._sentence_q.get()
            if sentence is None:
                break
            clean = _clean_for_tts(sentence)
            if not clean:
                continue
            played_any = False
            for piece in _wrap_for_kokoro(clean):
                for wav in _synthesize_kokoro_chunks(piece):
                    self._audio_q.put(("kokoro", wav))
                    played_any = True
            if not played_any:
                self._audio_q.put(("say", clean))
        self._audio_q.put(None)

    def speak(self, sentence: str) -> None:
        self._sentence_q.put(sentence)

    def join(self) -> None:
        """Signal end of input, then play everything the synthesizer already
        queued (or is still producing) until it signals completion."""
        self._sentence_q.put(None)
        player = _StreamPlayer()
        try:
            while True:
                item = self._audio_q.get()
                if item is None:
                    break
                kind, payload = item
                if self._t0 is not None and not self._logged_first_audio:
                    self._logged_first_audio = True
                    _log_lat("first audio played", self._t0)
                with _PLAYBACK_LOCK:  # never overlap a still-playing filler
                    if kind == "kokoro":
                        player.play(payload)
                    else:
                        # `say` has no shared-stream hook — closes the sox
                        # stream first so the two don't fight over the device.
                        player.close()
                        _fallback_speak(payload)
        finally:
            player.close()
        self._worker.join(timeout=5)


def _is_locked() -> bool:
    """Check if lock is held. Self-heals stale locks: if the PID in the lock
    file is dead OR the lock is older than 120s, remove it and return False.
    ponytail: timestamp+PID in one file, not fcntl — cross-platform, works
    on macOS where flock behaves differently with forked processes."""
    try:
        content = Path(LOCK_FILE).read_text().strip()
        pid, ts = int(content.split(",")[0]), float(content.split(",")[1])
        import time
        if time.time() - ts > 120:
            os.remove(LOCK_FILE)
            return False
        os.kill(pid, 0)  # check if process alive
        return True
    except (ValueError, OSError, FileNotFoundError):
        # corrupt/missing file or process dead — heal
        try:
            os.remove(LOCK_FILE)
        except FileNotFoundError:
            pass
        return False


def _lock() -> None:
    import time
    Path(LOCK_FILE).write_text(f"{os.getpid()},{time.time()}")


def _unlock() -> None:
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass


def _abort_stream(chat_id: str) -> None:
    """Hit the server's /chat/abort endpoint to kill a long-running stream."""
    import json
    import urllib.request as _req
    try:
        payload = json.dumps({"chat_id": chat_id}).encode()
        request = _req.Request(
            "http://127.0.0.1:9234/chat/abort", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        _req.urlopen(request, timeout=5)
    except Exception:
        pass


def _run_harness_blocking(text: str) -> str:
    """Hit the already-running warm chat_ui.py server (port 9234) instead of
    cold-importing harness.py fresh each invocation — that import tree (89+
    tool registry, LangGraph, clients) cost ~4.5s per wake-word trigger,
    consistently visible in brabble.log as a gap between hook exec and the
    first harness log line. Falls back to the in-process import if the
    server is down for any reason."""
    try:
        import json
        import urllib.request as _req

        payload = json.dumps({"message": text, "chat_id": CHAT_ID}).encode()
        request = _req.Request(
            "http://127.0.0.1:9234/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _req.urlopen(request, timeout=90) as resp:
            data = json.loads(resp.read())
        return data.get("reply", "")
    except Exception:
        import harness
        result, _model, _intent = harness.run(text, chat_id=CHAT_ID)
        return result


def _run_harness_streaming(text: str, t0: float | None = None) -> str:
    """Stream tokens from /chat/stream and speak each finished sentence as it
    arrives, so TTS starts on the first sentence instead of waiting for the
    whole reply to finish generating. Mirrors the sentence-chunked streaming
    TTS pattern telegram_bot.py already uses with Kokoro.

    Uses _TTSPipeline so sentence N+1 synthesizes while sentence N plays,
    instead of paying each sentence's synthesis time as dead air in between.
    """
    import json
    import urllib.parse
    import urllib.request as _req

    qs = urllib.parse.urlencode({"message": text, "chat_id": CHAT_ID})
    url = f"http://127.0.0.1:9234/chat/stream?{qs}"

    import random

    buf = ""
    parts: list[str] = []
    pipeline = _TTSPipeline(t0)
    logged_first_token = False
    logged_first_sentence = False

    # Mask dead air on slow (grounded) queries: speak a filler if no token has
    # arrived by _FILLER_AFTER_S. Cancelled on first token, so fast direct
    # answers never hear it.
    first_token_evt = threading.Event()

    def _maybe_filler() -> None:
        if first_token_evt.is_set():
            return
        with _PLAYBACK_LOCK:
            _speak(random.choice(_FILLERS))

    _filler_timer = threading.Timer(_FILLER_AFTER_S, _maybe_filler)
    _filler_timer.daemon = True
    _filler_timer.start()
    try:
        with _req.urlopen(url, timeout=90) as resp:
            if t0 is not None:
                _log_lat("stream connected", t0)
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[len("data: "):])
                if event.get("error"):
                    raise RuntimeError(event["error"])
                tok = _TOOL_PROGRESS_RE.sub("", event.get("token") or "")
                if tok:
                    if not first_token_evt.is_set():
                        first_token_evt.set()
                        _filler_timer.cancel()
                    if t0 is not None and not logged_first_token:
                        logged_first_token = True
                        _log_lat("first token", t0)
                    parts.append(tok)
                    buf += tok
                    ready, buf = sentence_boundary(buf)
                    if ready:
                        if t0 is not None and not logged_first_sentence:
                            logged_first_sentence = True
                            _log_lat("first sentence ready", t0)
                        pipeline.speak(ready)
                if event.get("done"):
                    break

        if buf.strip():
            pipeline.speak(buf.strip())
    finally:
        _filler_timer.cancel()
        pipeline.join()
    return "".join(parts)


def _run_harness(text: str, t0: float | None = None) -> str:
    """Speaks the reply itself (incrementally if streaming succeeds, or as one
    shot on fallback) — callers should not call _speak() again on the result."""
    try:
        return _run_harness_streaming(text, t0)
    except Exception:
        reply = _run_harness_blocking(text)
        if reply:
            _speak(reply)
        return reply


def _ingest_to_ui(query: str, reply: str) -> None:
    try:
        import json
        import urllib.request as _req
        _payload = json.dumps({"query": query, "reply": reply}).encode()
        _req.urlopen("http://127.0.0.1:9234/voice/ingest", data=_payload, timeout=2)
    except Exception:
        pass


def _poll_for_confirmation(stop_event: threading.Event) -> None:
    """Runs alongside _run_harness() — a blocked bash_exec confirmation
    (tools/confirmation.py) stalls the SSE stream with no token output, so this
    is the only way brabble notices one came up. Announces it via TTS once,
    writes the token to PENDING_CONFIRM_FILE so the *next* wake-word utterance
    (a spoken yes/no) can resolve it — see _resolve_pending_confirmation().
    """
    import json
    import urllib.request as _req

    seen: set[str] = set()
    while not stop_event.is_set():
        try:
            with _req.urlopen(
                "http://127.0.0.1:9234/chat/local-agent/pending-confirmations", timeout=5
            ) as resp:
                data = json.loads(resp.read())
            for p in data.get("pending", []):
                token = p.get("token", "")
                if token and token not in seen:
                    seen.add(token)
                    try:
                        with open(PENDING_CONFIRM_FILE, "w") as f:
                            json.dump(p, f)
                    except OSError:
                        pass
                    _speak(f"This needs your approval: {p.get('command', '')}. Say yes to approve, or no to cancel.")
        except Exception:
            pass
        stop_event.wait(CONFIRM_POLL_SEC)


def _looks_like_confirmation_reply(text: str) -> bool:
    return bool(_YES_RE.search(text) or _NO_RE.search(text))


def _resolve_pending_confirmation(text: str) -> None:
    """Handle a spoken yes/no for whatever _poll_for_confirmation flagged.
    Deliberately skips the busy-lock entirely — the original request is still
    holding it, blocked inside execute_tool() waiting on this exact decision.
    """
    import json
    import urllib.request as _req

    try:
        with open(PENDING_CONFIRM_FILE) as f:
            pending = json.load(f)
    except (OSError, ValueError):
        return
    finally:
        try:
            os.remove(PENDING_CONFIRM_FILE)
        except FileNotFoundError:
            pass

    approved = bool(_YES_RE.search(text)) and not _NO_RE.search(text)
    try:
        payload = json.dumps({"token": pending.get("token", ""), "approved": approved}).encode()
        request = _req.Request(
            "http://127.0.0.1:9234/chat/local-agent/confirm",
            data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        _req.urlopen(request, timeout=10)
    except Exception:
        pass
    _speak("Okay, running it." if approved else "Cancelled.")


def _verify_speaker(audio_path: str) -> bool:
    """Try the warm voiceprint daemon (core.py's voiceprint_daemon thread)
    first; on any failure, fall back to the cold inline resemblyzer import
    so a daemon hiccup never blocks legitimate use. Fails OPEN (returns True)
    if both paths error — verification erroring should never be the reason a
    real reply doesn't happen, matching the original try/except-pass
    semantics this replaces.
    """
    import json
    import urllib.request as _req

    try:
        payload = json.dumps({
            "audio_path": audio_path, "voiceprint_path": VOICEPRINT_PATH,
            "threshold": VERIFY_THRESHOLD,
        }).encode()
        request = _req.Request(
            "http://127.0.0.1:9238/verify", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with _req.urlopen(request, timeout=10) as resp:
            data = json.loads(resp.read())
        return bool(data.get("match", True))
    except Exception:
        pass

    try:
        from tools.voiceprint import verify as _verify
        match, _sim = _verify(audio_path, VOICEPRINT_PATH, threshold=VERIFY_THRESHOLD)
        return bool(match)
    except Exception:
        return True


def main() -> int:
    t0 = time.perf_counter()  # proxy for wake-word-heard; brabble execs this hook right after transcription
    text = (os.environ.get("BRABBLE_TEXT") or "").strip()
    if not text:
        return 0

    # ── Cancel previous playback + process ──
    # Fresh subprocess per wake-word. If a previous hook is alive (holding
    # lock, playing audio, or stuck in an LLM stream), kill it so the new
    # command can proceed immediately.
    #
    # Order matters:
    # 1. SIGKILL old hook → stops it from spawning more afplay children
    # 2. pkill afplay/say → kills orphaned audio from old hook
    # 3. _abort_stream → kills server-side SSE so old hook's urllib read dies
    # 4. Force-remove lock → clears stale lock (old finally block can't run
    #    after SIGKILL, and even if it could, os.remove is idempotent)
    try:
        content = Path(LOCK_FILE).read_text().strip()
        old_pid = int(content.split(",")[0])
        if old_pid != os.getpid():
            import signal
            os.kill(old_pid, signal.SIGKILL)
            sys.stderr.write(f"[brabble_hook] killed previous hook pid={old_pid}\n")
    except (ValueError, OSError, FileNotFoundError):
        pass
    subprocess.run(["pkill", "-f", "afplay"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-x", "say"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _abort_stream(CHAT_ID)
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass

    sys.stderr.write(f"[brabble_hook] heard: {text!r}\n")

    # ── Resolve a pending confirmation before anything else — the original
    # request is still holding LOCK_FILE, blocked on this exact decision, so
    # this must bypass the busy-gate below rather than get rejected by it. ──
    if os.path.exists(PENDING_CONFIRM_FILE) and _looks_like_confirmation_reply(text):
        _resolve_pending_confirmation(text)
        return 0

    # ── Speaker verification ──
    audio_path = os.environ.get("BRABBLE_AUDIO_PATH", "").strip()
    if audio_path and os.path.exists(VOICEPRINT_PATH) and os.path.exists(audio_path):
        if not _verify_speaker(audio_path):
            sys.stderr.write("[brabble_hook] voiceprint rejected\n")
            return 0

    # ── Check if harness is already busy ──
    if _is_locked():
        sys.stderr.write("[brabble_hook] harness busy, rejecting utterance\n")
        if sys.platform == "darwin":
            subprocess.Popen(["say", "busy"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        return 0

    # ── Acquire lock and process ──
    _lock()
    _log_lat("lock acquired", t0)
    stop_poll = threading.Event()
    poller = threading.Thread(target=_poll_for_confirmation, args=(stop_poll,), daemon=True)
    poller.start()
    # ponytail: hard timeout — voice users can't wait for browser agents
    # doing 20+ LLM rounds. Abort the server-side stream so the lock releases.
    # 2026-07-11: was 45.0s — raised to 60s. At 45s this fired
    # on legitimate multi-tool-call answers (confirmed live: 54-73s totals for
    # correct, non-hanging replies), truncating them mid-word before the
    # cloud_client.py OpenRouter-fallback fix (same date) existed, and even
    # after that fix an abort mid-generation still means throwing away partial
    # work and restarting on a fallback model. 60s trades a bit more worst-case
    # wait for meaningfully fewer of these truncate-and-restart cycles.
    _abort_timer = threading.Timer(60.0, _abort_stream, args=(CHAT_ID,))
    _abort_timer.daemon = True
    _abort_timer.start()
    try:
        reply = _run_harness(text, t0)
        if reply:
            _ingest_to_ui(text, reply)
    finally:
        _log_lat("done (tts drained)", t0)
        _abort_timer.cancel()
        stop_poll.set()
        try:
            os.remove(PENDING_CONFIRM_FILE)
        except FileNotFoundError:
            pass
        _unlock()

    if reply:
        print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
