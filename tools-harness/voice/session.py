"""Continuous listening session: mic → VAD → streaming ASR → agent.

This is the piece that makes the mic behave like a person's: **click the mic and
it listens until you click it off**, across as many turns as you take, while the
bunny keeps acting on screen. Push-to-talk is just `feed_pcm()` with the client
deciding when frames flow.

Structure (all on one audio worker thread, so `feed_pcm` never blocks a socket):

    frame ──► VAD ──► utterance buffer ──► ASR partials (every ~350 ms)
                        │
                        └─ endpoint (silence) ─► final ─► agent turn (own thread)
                                                         │
                                       token/audio/done ◄┘ → on_event()

Barge-in: speech detected while a reply is streaming cancels that turn
(`agent.cancel()`, which sets the brain thread's `abort_event`) and the new
utterance starts immediately — that is what "talk over it" has to feel like.

VAD note: `webrtcvad` cannot even be imported on Python 3.14 (it still requires
`pkg_resources`), so the default detector is an adaptive energy gate: a rolling
noise floor from the last ~2 s and a speech threshold at `max(floor*3, 0.004)`
with 2-frame onset and ~200 ms hangover. It is deterministic, dependency-free
and injectable (`speech_detector=`) for tests and for a future Silero model.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

from voice import audio, stt
from voice.agent import VOICE_CHAT_ID, VoiceAgent, VoiceEvent, get_agent

_log = logging.getLogger("voice.session")

FRAME_MS = 30
FRAME_SAMPLES = audio.SAMPLE_RATE * FRAME_MS // 1000          # 480 @ 16 kHz
FRAME_BYTES = FRAME_SAMPLES * 2

# Endpointing / utterance shaping (all overridable per session).
SILENCE_MS = 700          # trailing silence that ends an utterance
MIN_SPEECH_MS = 240       # shorter than this is a cough/click, not a turn
MAX_UTTERANCE_S = 20.0    # force an endpoint so the caption can't run away
PREROLL_MS = 240          # audio kept from before onset, so first words survive
PARTIAL_EVERY_MS = 350    # how often the ASR gets a chunk while you talk


class EnergyVad:
    """Adaptive energy gate. `__call__(pcm16_frame) -> bool` (speech?)."""

    def __init__(self, *, floor_window_s: float = 2.0, min_rms: float = 0.004,
                 ratio: float = 3.0, onset_frames: int = 2, hang_frames: int = 6) -> None:
        self.min_rms = min_rms
        self.ratio = ratio
        self.onset_frames = onset_frames
        self.hang_frames = hang_frames
        self._history: deque[float] = deque(maxlen=int(floor_window_s * 1000 / FRAME_MS))
        self._onset = 0
        self._hang = 0

    def rms(self, frame: bytes) -> float:
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
        return float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0

    def __call__(self, frame: bytes) -> bool:
        level = self.rms(frame)
        floor = float(np.median(self._history)) if self._history else self.min_rms
        threshold = max(self.min_rms, floor * self.ratio)
        self._history.append(level)
        if level > threshold:
            self._onset += 1
            self._hang = self.hang_frames
        else:
            self._onset = 0
            self._hang = max(0, self._hang - 1)
        if self._onset >= self.onset_frames:
            return True
        return self._hang > 0


def default_detector() -> Callable[[bytes], bool]:
    try:  # nice-to-have: py-webrtcvad if the environment can import it
        import webrtcvad  # noqa: F401

        vad = webrtcvad.Vad(2)
        return lambda frame: vad.is_speech(frame, audio.SAMPLE_RATE)
    except Exception:
        return EnergyVad()


class VoiceSession:
    """A listening session you switch on once and switch off when you're done.

    `start()` opens the mic and keeps it open across turns; `stop()` is the
    toggle-off. A client that owns the mic (the desktop app) calls
    `start(mic=False)` and streams `feed_pcm()` instead — identical behaviour,
    including barge-in, because everything downstream of the frames is shared.
    """

    def __init__(
        self,
        *,
        agent: Optional[VoiceAgent] = None,
        asr=None,
        chat_id: str = VOICE_CHAT_ID,
        on_event: Optional[Callable[[dict], None]] = None,
        speech_detector: Optional[Callable[[bytes], bool]] = None,
        silence_ms: int = SILENCE_MS,
        min_speech_ms: int = MIN_SPEECH_MS,
        max_utterance_s: float = MAX_UTTERANCE_S,
        partial_every_ms: int = PARTIAL_EVERY_MS,
        play_audio: bool = False,
        device=None,
    ) -> None:
        self.agent = agent or get_agent()
        self._asr = asr
        self.chat_id = chat_id
        self.on_event = on_event or (lambda event: None)
        self.detector = speech_detector or default_detector()
        self.silence_ms = silence_ms
        self.min_speech_ms = min_speech_ms
        self.max_utterance_s = max_utterance_s
        # >= 500 ms: one ASR push costs ~320-400 ms on an M4, so a tighter hop
        # backs the audio queue up instead of feeling faster.
        self.partial_every_ms = max(partial_every_ms, 500)
        self.play_audio = play_audio
        self.device = device

        self._q: queue.Queue = queue.Queue(maxsize=512)
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._mic_stream = None
        self._mic_thread: Optional[threading.Thread] = None
        self._listening = False

        # utterance state (worker thread only)
        self._preroll: deque = deque(maxlen=max(1, PREROLL_MS // FRAME_MS))
        self._frames: list[bytes] = []
        self._pending: list[np.ndarray] = []
        self._speech_ms = 0
        self._silence_ms = 0
        self._in_utterance = False
        self._turn = None

        self.state = "idle"
        self.last_rms = 0.0
        self.last_partial = ""
        self.last_final = ""
        self.turns = 0

    # ── public API ─────────────────────────────────────────────────────────

    @property
    def is_listening(self) -> bool:
        return self._listening

    def start(self, *, mic: bool = True) -> bool:
        """Turn listening on. Keeps listening until `stop()`. False if no mic."""
        self._stop.clear()
        self._ensure_worker()
        self._listening = True
        self._set_state("listening")
        if mic and not self._open_mic():
            self._listening = False
            self._set_state("idle")
            return False
        return True

    def stop(self) -> None:
        """Toggle off: close the mic, drop the queue, cancel any running turn."""
        self._listening = False
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None
        self._mic_thread = None  # the capture loop exits on _stop below
        runner = self._turn
        if runner is not None:
            runner.cancel()
        self._reset_utterance()
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except Exception:
            pass
        self._set_state("idle")

    def feed_pcm(self, pcm16: bytes) -> None:
        """Push mic audio from another transport (WS/app). Never blocks the caller."""
        if not pcm16:
            return
        if self._worker is None or not self._worker.is_alive():
            self._ensure_worker()
        view = memoryview(pcm16)
        for off in range(0, len(view) - FRAME_BYTES + 1, FRAME_BYTES):
            try:
                self._q.put_nowait(bytes(view[off : off + FRAME_BYTES]))
            except queue.Full:  # consumer fell behind: drop, never grow latency
                return

    def status(self) -> dict:
        return {
            "listening": self._listening,
            "state": self.state,
            "chat_id": self.chat_id,
            "turns": self.turns,
            "last_rms": round(self.last_rms, 5),
            "last_partial": self.last_partial,
            "last_final": self.last_final,
            "mic": self.device or audio.input_device_name(),
            "busy": self._turn is not None,
        }

    # ── internals (worker thread) ──────────────────────────────────────────

    def _emit(self, payload: dict) -> None:
        try:
            self.on_event(payload)
        except Exception:  # a bad subscriber must never kill the audio loop
            _log.warning("[voice.session] on_event failed", exc_info=True)

    def _set_state(self, state: str) -> None:
        self.state = state
        self._emit({"type": "state", "state": state, "listening": self._listening})

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="voice-session")
        self._worker.start()

    def _get_asr(self):
        if self._asr is None:
            from voice import streaming_asr

            self._asr = streaming_asr.get_asr()
        return self._asr

    def _open_mic(self) -> bool:
        try:
            self._mic_stream = audio.open_input(device=self.device)
            self._mic_stream.start()
        except Exception as exc:
            self._emit({"type": "error", "text": f"mic unavailable: {exc}"})
            return False

        def capture() -> None:
            silent = 0
            while not self._stop.is_set() and self._mic_stream is not None:
                try:
                    data, _overflowed = self._mic_stream.read(FRAME_SAMPLES)
                except Exception:
                    break
                pcm = np.asarray(data, dtype=np.int16).tobytes()
                level = float(
                    np.sqrt(np.mean(np.frombuffer(pcm, dtype="<i2").astype(np.float32) ** 2))
                ) / 32768.0
                silent = silent + 1 if level == 0.0 else 0
                if silent == 100:  # ~3 s of digital silence — TCC denial looks like this
                    self._emit(
                        {
                            "type": "warning",
                            "text": "microphone is delivering silence — check Privacy & Security → Microphone",
                        }
                    )
                self.feed_pcm(pcm)

        self._mic_thread = threading.Thread(target=capture, daemon=True, name="voice-mic")
        self._mic_thread.start()
        return True

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            if frame is None:
                break
            try:
                self._on_frame(frame)
            except Exception:
                _log.exception("[voice.session] frame handling failed")

    def _on_frame(self, frame: bytes) -> None:
        level = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
        self.last_rms = float(np.sqrt(np.mean(level * level))) if len(level) else 0.0
        speech = bool(self.detector(frame))

        if not self._in_utterance:
            self._preroll.append(frame)
            if speech:
                if self._turn is not None:  # talking over the reply / while it acts
                    self._barge_in()
                self._begin_utterance()
            return

        self._frames.append(frame)
        self._feed_asr(frame)
        if speech:
            self._speech_ms += FRAME_MS
            self._silence_ms = 0
        else:
            self._silence_ms += FRAME_MS
        if self._silence_ms >= self.silence_ms:
            self._end_utterance()
        elif len(self._frames) * FRAME_MS / 1000.0 >= self.max_utterance_s:
            self._end_utterance(forced=True)

    def _begin_utterance(self) -> None:
        self._in_utterance = True
        self._frames = list(self._preroll)   # keep the first syllable
        self._preroll.clear()
        self._pending = []
        self._speech_ms = 0
        self._silence_ms = 0
        self.last_partial = ""
        self._set_state("hearing")
        try:
            self._get_asr().start_utterance().result(timeout=10)
        except Exception:
            _log.warning("[voice.session] asr start failed", exc_info=True)

    def _feed_asr(self, frame: bytes) -> None:
        self._pending.append(np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0)
        if len(self._pending) * FRAME_MS < self.partial_every_ms:
            return
        chunk = np.concatenate(self._pending)
        self._pending = []
        try:
            update = self._get_asr().push(chunk)
        except Exception:
            _log.warning("[voice.session] asr push failed", exc_info=True)
            return
        text = (update.stable or update.text or "").strip()
        if text and text != self.last_partial:
            self.last_partial = text
            self._emit({"type": "partial", "text": text, "stable": update.stable})

    def _end_utterance(self, *, forced: bool = False) -> None:
        self._in_utterance = False
        speech_ms = self._speech_ms
        frames_ms = len(self._frames) * FRAME_MS
        self._reset_utterance()
        if frames_ms == 0 or speech_ms < self.min_speech_ms:
            self._emit({"type": "ignored", "reason": "too_short"})
            self._set_state("listening" if self._listening else "idle")
            return
        try:
            text = (self._get_asr().finalize() or "").strip()
        except Exception as exc:
            self._emit({"type": "error", "text": f"transcription failed: {exc}"})
            self._set_state("listening" if self._listening else "idle")
            return
        if stt.is_error(text):
            self._emit({"type": "error", "text": f"could not transcribe ({text})"})
            self._set_state("listening" if self._listening else "idle")
            return

        self.last_final = text
        self.turns += 1
        self._emit({"type": "final", "text": text, "forced": forced})
        self._dispatch_turn(text)

    def _dispatch_turn(self, text: str) -> None:
        try:
            runner = self.agent.begin_turn(query=text, chat_id=self.chat_id, tts_enabled=True)
        except Exception as exc:
            self._emit({"type": "error", "text": f"agent unavailable: {exc}"})
            self._set_state("listening" if self._listening else "idle")
            return
        self._turn = runner
        self._set_state("thinking")
        threading.Thread(
            target=self._pump_turn, args=(runner,), daemon=True, name=f"voice-turn-{runner.turn_id}"
        ).start()

    def _pump_turn(self, runner) -> None:
        try:
            for event in runner.events():
                if event.kind == "audio":
                    self._set_state("speaking")
                    if self.play_audio:
                        audio.play_wav_bytes(event.data, blocking=False)
                self._emit(event.as_dict())
                if event.kind in ("done", "error", "cancelled"):
                    break
        except Exception:
            _log.exception("[voice.session] turn pump failed")
        finally:
            if self._turn is runner:
                self._turn = None
            if self._listening:
                self._set_state("listening")
            else:
                self._set_state("idle")

    def _barge_in(self) -> None:
        runner = self._turn
        if runner is None:
            return
        _log.info("[voice.session] barge-in: cancelling turn %s", runner.turn_id)
        runner.cancel()
        self._turn = None
        self._emit({"type": "interrupt", "turn_id": runner.turn_id})

    def _reset_utterance(self) -> None:
        self._in_utterance = False
        self._frames = []
        self._pending = []
        self._speech_ms = 0
        self._silence_ms = 0
        self._preroll.clear()
        self.last_partial = ""


_SESSION: Optional[VoiceSession] = None
_SESSION_LOCK = threading.Lock()


def get_session(**kwargs) -> VoiceSession:
    """Process-wide listening session (one mic, one toggle)."""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = VoiceSession(**kwargs)
        return _SESSION


class VoiceBus:
    """Fan-out for every voice payload (session + agent) to SSE consumers.

    One producer set (the session's audio worker, the turn pumps), many
    consumers (orb, web UI, app panels). Slow consumers are dropped rather than
    allowed to back-pressure the audio path.
    """

    def __init__(self, maxsize: int = 128) -> None:
        self._maxsize = maxsize
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, payload: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                self.unsubscribe(q)

    __call__ = publish

    def listeners(self) -> int:
        with self._lock:
            return len(self._subs)


_BUS: Optional[VoiceBus] = None
_BUS_LOCK = threading.Lock()


def get_bus() -> VoiceBus:
    global _BUS
    with _BUS_LOCK:
        if _BUS is None:
            _BUS = VoiceBus()
        return _BUS
