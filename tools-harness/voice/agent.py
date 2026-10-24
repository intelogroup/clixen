"""The voice agent: one turn lifecycle, event by event, cancellable.

This is the piece every surface shares (orb, web UI, wake listener, PTT):

    transcript → token* → audio* → done        (or → error / cancelled)

Design rules that come from the live bugs this codebase already paid for:

* **The brain is injected, never imported.** `chat_ui` hands us `harness.run`,
  the wake listener hands us the same thing, a test hands us a lambda. That
  keeps `voice` importable without the orchestrator and makes the whole turn
  testable without a model.
* **Cancellation is the thread's `abort_event`.** `cancellation.check_aborted()`
  and `cloud_client`'s soft timeout already read
  `getattr(threading.current_thread(), "abort_event", None)`, so a barge-in sets
  that on the brain thread and the in-flight LLM/tool loop unwinds by itself.
  There is no second cancellation mechanism.
* **A newer turn wins.** `begin_turn()` cancels the previous turn for the same
  chat_id (barge-in), which is what a person expects when they start talking
  over the reply. Queueing an older turn behind the new one is what the deleted
  `harness._TURN_QUEUE` did, and it is why "stop" felt dead.
* **TTS failure is not turn failure.** `voice.tts` returns `b""` when every
  engine is down; the turn still delivers text tokens and a `done`.
"""
from __future__ import annotations

import base64
import os
import queue
import threading
import uuid
import logging
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

from voice import stt, tts

_log = logging.getLogger("voice.agent")

# The chat id every voice surface writes history under. (Was "brabble_voice",
# a dead channel; harness.py keys its voice tuning — round cap, 28 s round
# timeout, short history window, plain-speech prompt — off this value.)
VOICE_CHAT_ID = "voice_agent"

DEFAULT_TURN_TIMEOUT = 300.0

# Kept as module-level names for callers/tests that already import them from
# here; the implementation lives in voice.tts (single speech pipeline, see
# tts.SpeechStream for why).
strip_progress_labels = tts.strip_progress_labels


@dataclass
class VoiceEvent:
    """One frame of a turn. `data` is raw WAV bytes; `as_dict` base64s it."""

    kind: str                      # transcript | token | audio | done | error | cancelled
    text: str = ""
    data: Optional[bytes] = None
    turn_id: str = ""
    model: str = ""

    def as_dict(self) -> dict:
        out: dict = {"type": self.kind, "turn_id": self.turn_id}
        if self.text:
            out["text"] = self.text
        if self.data:
            out["data"] = base64.b64encode(self.data).decode()
        if self.model:
            out["model"] = self.model
        return out


TokenFilter = tuple[Callable[[str], str], Callable[[], str]]


class TurnRunner:
    """One turn, one event per `next_event()` call, from any thread.

    All the slow work (STT, the brain wait, per-sentence TTS) happens inside
    `next_event()`, so a caller can put the whole turn on a worker thread
    (`stream_turn`) or behind `asyncio.to_thread` (`astream_turn`) and never
    block its own event loop.
    """

    def __init__(
        self,
        agent: "VoiceAgent",
        *,
        audio: Optional[bytes] = None,
        suffix: str = ".webm",
        query: Optional[str] = None,
        chat_id: str = VOICE_CHAT_ID,
        tts_enabled: bool = True,
        web_search: bool = False,
        turn_id: str = "",
        token_filter: Optional[TokenFilter] = None,
        voice: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.agent = agent
        self.audio = audio
        self.suffix = suffix or ".webm"
        self.query = (query or "").strip() or None
        self.chat_id = chat_id
        self.tts_enabled = tts_enabled
        self.web_search = web_search
        self.turn_id = turn_id or uuid.uuid4().hex[:12]
        self.token_filter = token_filter
        self.voice = voice or agent.tts_voice
        self.timeout = timeout or agent.timeout

        self._abort = threading.Event()      # handed to the brain thread
        self._token_q: Optional[queue.Queue] = None
        self._gen: Optional[Iterator[VoiceEvent]] = None
        self._lock = threading.Lock()
        self._cancelled = False
        self._filler_sent = False
        self.transcript = ""

    # ── public ─────────────────────────────────────────────────────────────

    def next_event(self) -> Optional[VoiceEvent]:
        """Block for the next frame; None when the turn is over."""
        with self._lock:
            if self._gen is None:
                self._gen = self._run()
            try:
                return next(self._gen)
            except StopIteration:
                return None

    def cancel(self) -> None:
        """Barge-in: stop the brain, wake the token loop, stop playback."""
        self._cancelled = True
        self._abort.set()
        q = self._token_q
        if q is not None:
            try:
                q.put_nowait(None)
            except Exception:
                pass
        try:
            from voice import audio as _audio

            _audio.stop_playback()
        except Exception:
            pass

    def events(self) -> Iterator[VoiceEvent]:
        while (ev := self.next_event()) is not None:
            yield ev

    # ── the turn itself ────────────────────────────────────────────────────

    def _run(self) -> Iterator[VoiceEvent]:
        if self.query is None:
            if not self.audio:
                yield VoiceEvent("error", text="no audio and no text", turn_id=self.turn_id)
                return
            text = stt.transcribe_bytes(self.audio, self.suffix)
            if stt.is_error(text):
                yield VoiceEvent(
                    "error", text=f"could not transcribe ({text})", turn_id=self.turn_id
                )
                return
            self.query = text
            self.transcript = text
            yield VoiceEvent("transcript", text=text, turn_id=self.turn_id)

        token_q: queue.Queue = queue.Queue()
        self._token_q = token_q
        result: dict = {}
        brain = self.agent._brain
        if brain is None:  # pragma: no cover - begin_turn() refuses without one
            yield VoiceEvent("error", text="no brain configured", turn_id=self.turn_id)
            return

        def on_token(token: str) -> None:
            token_q.put(token)

        def run_brain() -> None:
            try:
                out = brain(
                    self.query,
                    chat_id=self.chat_id,
                    on_token=on_token,
                    turn_id=self.turn_id,
                    force_web_search=self.web_search,
                )
                if not out:
                    result["error"] = "the orchestrator returned no reply"
                else:
                    result["reply"], result["model"] = out[0], out[1]
            except Exception as exc:
                from clients.cancellation import QueryAbortedException

                if isinstance(exc, QueryAbortedException):
                    result["cancelled"] = True
                else:
                    _log.exception("[voice] brain failed turn=%s", self.turn_id)
                    result["error"] = str(exc)
            finally:
                token_q.put(None)

        thread = threading.Thread(target=run_brain, daemon=True, name=f"voice-brain-{self.turn_id}")
        # The contract cancellation.check_aborted() and cloud_client already
        # read: abort this event and the in-flight LLM/tool loop unwinds.
        thread.abort_event = self._abort
        thread.start()

        filter_token, flush_tail = self.token_filter or (lambda t: t, lambda: "")
        speech = tts.SpeechStream()
        saw_token = False
        if self.tts_enabled:
            self.agent._prime_fillers()
        try:
            while True:
                # First wait is short: if the brain is slow, say something.
                timeout = self.timeout
                if (
                    not saw_token
                    and not self._filler_sent
                    and self.tts_enabled
                    and self.agent.filler_after_s > 0
                ):
                    timeout = min(timeout, self.agent.filler_after_s)
                try:
                    token = token_q.get(timeout=timeout)
                except queue.Empty:
                    if not saw_token and self.tts_enabled and not self._filler_sent:
                        self._filler_sent = True
                        filler = self.agent._next_filler()
                        if filler:
                            yield VoiceEvent("audio", data=filler, turn_id=self.turn_id)
                        continue  # now wait the full turn timeout
                    result["error"] = "model response timed out"
                    break
                saw_token = True
                if token is None:
                    tail = flush_tail()
                    if tail:
                        yield VoiceEvent("token", text=tail, turn_id=self.turn_id)
                    if self.tts_enabled:
                        # Everything still unsaid: whatever the held-back tail
                        # completes, plus the last unterminated sentence.
                        for text in ((speech.feed(tail) if tail else ""), speech.flush()):
                            if text:
                                wav = tts.synthesize_cached(text, self.voice)
                                if wav:
                                    yield VoiceEvent("audio", data=wav, turn_id=self.turn_id)
                    break
                visible = filter_token(token)
                if visible:
                    yield VoiceEvent("token", text=visible, turn_id=self.turn_id)
                if self.tts_enabled:
                    ready = speech.feed(visible)
                    if ready:
                        wav = tts.synthesize_cached(ready, self.voice)
                        if wav:
                            yield VoiceEvent("audio", data=wav, turn_id=self.turn_id)
        finally:
            self.agent._release(self.chat_id, self)

        if self._cancelled or result.get("cancelled"):
            yield VoiceEvent("cancelled", turn_id=self.turn_id)
        elif result.get("error"):
            yield VoiceEvent("error", text=result["error"], turn_id=self.turn_id)
        else:
            yield VoiceEvent("done", model=result.get("model", ""), turn_id=self.turn_id)


class VoiceAgent:
    """One brain, one active turn per chat, one event vocabulary.

    Surfaces differ only in how they move bytes: the orb and the web UI stream
    events over HTTP, the desktop app holds a duplex socket, the wake listener
    plays audio itself. They all call `stream_turn()`/`astream_turn()`.
    """

    def __init__(
        self,
        *,
        tts_voice: str = tts.DEFAULT_VOICE,
        timeout: float = DEFAULT_TURN_TIMEOUT,
        filler_texts: tuple[str, ...] | None = None,
        filler_after_s: float | None = None,
    ) -> None:
        self._brain: Optional[Callable] = None
        self.tts_voice = tts_voice
        self.timeout = timeout
        # Latency mask: the brain's first token is the long pole (measured 1.5-4 s
        # on the cloud path). If nothing has been said by `filler_after_s`, speak a
        # pre-synthesised acknowledgement so the line is never silent. The wavs are
        # primed in the background at the first turn, so this costs 0 ms on the spot.
        self.filler_after_s = (
            filler_after_s
            if filler_after_s is not None
            else (0.0 if os.environ.get("CLIXEN_VOICE_FILLER", "1") == "0" else 0.6)
        )
        self.filler_texts = (
            filler_texts
            if filler_texts is not None
            else tuple(
                t.strip()
                for t in os.environ.get(
                    "CLIXEN_VOICE_FILLERS", "Let me check.|One moment."
                ).split("|")
                if t.strip()
            )
        )
        self._filler_wavs: dict[str, bytes] = {}
        self._filler_primed = False
        self._filler_index = 0
        self._lock = threading.Lock()
        self._active: dict[str, TurnRunner] = {}
        self._subscribers: set[queue.Queue] = set()

    # ── latency mask ───────────────────────────────────────────────────────

    def _prime_fillers(self) -> None:
        if self._filler_primed or not self.filler_texts or self.filler_after_s <= 0:
            return
        self._filler_primed = True

        def run() -> None:
            for text in self.filler_texts:
                try:
                    wav = tts.synthesize_cached(text, self.tts_voice)
                    if wav:
                        self._filler_wavs[text] = wav
                except Exception:
                    pass

        threading.Thread(target=run, daemon=True, name="voice-filler-prime").start()

    def _next_filler(self) -> bytes:
        """Rotate through the primed fillers; empty until priming finishes."""
        if not self._filler_wavs:
            return b""
        with self._lock:
            keys = list(self._filler_wavs)
            text = keys[self._filler_index % len(keys)]
            self._filler_index += 1
        return self._filler_wavs[text]

    # ── wiring ─────────────────────────────────────────────────────────────

    def set_brain(self, brain: Callable) -> None:
        """`brain(query, *, chat_id, on_token, turn_id, force_web_search) -> (reply, model, intent)`."""
        self._brain = brain

    @property
    def ready(self) -> bool:
        return self._brain is not None

    # ── turn registry (barge-in) ───────────────────────────────────────────

    def _register(self, chat_id: str, runner: TurnRunner) -> None:
        with self._lock:
            previous = self._active.get(chat_id)
            self._active[chat_id] = runner
        if previous is not None and previous is not runner:
            _log.info("[voice] barge-in on %s: cancelling turn %s", chat_id, previous.turn_id)
            previous.cancel()

    def _release(self, chat_id: str, runner: TurnRunner) -> None:
        with self._lock:
            if self._active.get(chat_id) is runner:
                self._active.pop(chat_id, None)

    def cancel(self, chat_id: str = VOICE_CHAT_ID) -> bool:
        """Stop whatever is running for this chat. True if something was."""
        with self._lock:
            runner = self._active.get(chat_id)
        if runner is None:
            return False
        runner.cancel()
        return True

    def is_busy(self, chat_id: str = VOICE_CHAT_ID) -> bool:
        with self._lock:
            return chat_id in self._active

    def active_turn(self, chat_id: str = VOICE_CHAT_ID) -> str:
        with self._lock:
            runner = self._active.get(chat_id)
        return runner.turn_id if runner else ""

    # ── turns ──────────────────────────────────────────────────────────────

    def begin_turn(self, **kwargs) -> TurnRunner:
        """Create (and register) a turn. Cancels any turn already running."""
        if self._brain is None:
            raise RuntimeError("VoiceAgent has no brain — call set_brain() first")
        chat_id = kwargs.get("chat_id", VOICE_CHAT_ID)
        runner = TurnRunner(self, **kwargs)
        self._register(chat_id, runner)
        return runner

    def stream_turn(self, **kwargs) -> Iterator[VoiceEvent]:
        """Sync generator — for callers already on a worker thread."""
        runner = self.begin_turn(**kwargs)
        yield from runner.events()

    async def astream_turn(self, **kwargs):
        """Async generator — heavy steps run via asyncio.to_thread."""
        import asyncio

        runner = self.begin_turn(**kwargs)
        while True:
            event = await asyncio.to_thread(runner.next_event)
            if event is None:
                return
            yield event

    def speak_events(self, text: str, voice: Optional[str] = None, turn_id: str = "") -> Iterator[VoiceEvent]:
        """Voice a text-only reply (typed input has no audio of its own)."""
        tid = turn_id or uuid.uuid4().hex[:12]
        for wav in tts.iter_wavs(text, voice or self.tts_voice):
            yield VoiceEvent("audio", data=wav, turn_id=tid)
        yield VoiceEvent("done", turn_id=tid)

    # ── fan-out for listeners (wake agent, app panels) ─────────────────────

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: VoiceEvent) -> None:
        """Push an event to every subscriber; drop the ones that stop reading."""
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                self.unsubscribe(q)


_AGENT: Optional[VoiceAgent] = None
_AGENT_LOCK = threading.Lock()


def get_agent(**kwargs) -> VoiceAgent:
    """Process-wide agent (one brain, one set of subscribers)."""
    global _AGENT
    with _AGENT_LOCK:
        if _AGENT is None:
            _AGENT = VoiceAgent(**kwargs)
        return _AGENT
