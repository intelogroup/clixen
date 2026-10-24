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

VAD note: three detectors, best first.
  * Silero v5 (ONNX, ~2 MB, CPU, ~1 ms per 30 ms frame) — a trained
    speech/non-speech classifier with a probability output. Vendored at
    `voice/models/silero_vad.onnx`; needs `onnxruntime` only (not torch).
  * `webrtcvad` (GMM, 30 ms frames) — decent, no probabilities.
  * `EnergyVad` — an adaptive RMS gate, the last resort. It cannot tell a fan
    from a voice: any broadband noise above `max(floor*3, 0.004)` (= -48 dBFS)
    opens an utterance, and the ASR then hallucinates on it ("Yeah.", "Okay.").
    This used to be what ran here, silently, because `webrtcvad`'s import failed
    on Python 3.14 (`pkg_resources`) and `default_detector()` swallowed it.

All three sit behind one seam: a detector is anything with
`is_speech(frame) -> bool`; plain callables are wrapped, and `last_p` exposes the
probability (1.0/0.0 when the backend has none) for barge-in gating.
"""
from __future__ import annotations

import logging
import queue
import shutil
import threading
import urllib.request
from collections import deque
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from voice import audio, stt
from voice.agent import VOICE_CHAT_ID, VoiceAgent, get_agent

_log = logging.getLogger("voice.session")

FRAME_MS = 30
FRAME_SAMPLES = audio.SAMPLE_RATE * FRAME_MS // 1000          # 480 @ 16 kHz
FRAME_BYTES = FRAME_SAMPLES * 2

# Endpointing / utterance shaping (all overridable per session).
SILENCE_MS = 700          # trailing silence that ends an utterance
SILENCE_MS_QUESTION = 500  # a partial ending in "?" closes faster — the user is done
MIN_SPEECH_MS = 240       # shorter than this is a cough/click, not a turn
MAX_UTTERANCE_S = 20.0    # force an endpoint so the caption can't run away
PREROLL_MS = 240          # audio kept from before onset, so first words survive
PARTIAL_EVERY_MS = 350    # how often the ASR gets a chunk while you talk

# Turn-taking guards.
LOW_VOICED_RATIO = 0.25   # below this the "utterance" was mostly noise
BARGE_IN_P = 0.75         # interrupting a reply needs a *confident* speech frame
BARGE_IN_FRAMES = 5       # ...for 150 ms, so a door slam can't cancel a turn
HALLUCINATION_VOICED = 0.35  # noise-shaped finals only get dropped when this thin

# Short non-answers ASR produces out of room tone. Only dropped when the
# utterance was mostly noise (see HALLUCINATION_VOICED), never on their own.
HALLUCINATIONS = {
    "yeah", "yes", "okay", "ok", "mm hmm", "uh huh", "hmm", "um", "uh",
    "thank you", "thanks", "bye", "you", "the", "so",
    "c'est quoi", "je ne sais pas",
}


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


MODEL_DIR = Path(__file__).resolve().parent / "models"
SILERO_MODEL = MODEL_DIR / "silero_vad.onnx"
SILERO_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/v5.1.2/"
    "src/silero_vad/data/silero_vad.onnx"
)


def _ensure_silero_model(path: Path = SILERO_MODEL) -> Path:
    """Fetch the Silero weights once (~2.2 MB). `models/` is git-ignored.

    Raises on failure (no network, offline box) so `default_detector()` can fall
    back to webrtcvad instead of blocking the session.
    """
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".onnx.part")
    with urllib.request.urlopen(SILERO_URL, timeout=60) as response:
        with open(partial, "wb") as handle:
            shutil.copyfileobj(response, handle)
    partial.replace(path)
    _log.info("[voice.session] fetched Silero VAD weights -> %s", path)
    return path


class SileroVad:
    """Silero v5 (ONNX): a trained speech/non-speech classifier.

    The model is recurrent — it carries a `(2, 1, 128)` state plus the last 64
    samples of the previous chunk (its "context") and scores 512-sample chunks.
    Mic frames are 480 samples, so they are buffered and each call reports the
    verdict for the most recent complete chunk.

    Hysteresis matters more than the raw threshold: opening at 0.5 and closing
    only below 0.35 (plus a two-frame hangover) keeps word gaps from reading as
    end-of-turn, which is what makes the endpoint feel human.
    """

    CHUNK = 512
    CONTEXT = 64
    HANG_FRAMES = 2
    open_p = 0.5
    close_p = 0.35
    name = "silero"

    def __init__(self, model_path: Optional[Path] = None) -> None:
        import onnxruntime as ort

        path = Path(model_path) if model_path else _ensure_silero_model()
        if not path.exists():
            raise FileNotFoundError(f"silero_vad.onnx missing at {path}")
        # One intra-op thread: 512 samples is tiny, and a thread pool costs more
        # than the matmuls it would spread.
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        self._sess = ort.InferenceSession(
            str(path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._sr = np.array(audio.SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._ctx = np.zeros(self.CONTEXT, dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)
        self._open = False
        self._hang = 0
        self.last_p = 0.0

    def is_speech(self, frame: bytes) -> bool:
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
        pieces = [self._buf, samples] if self._buf.size else [samples]
        self._buf = np.concatenate(pieces)
        while self._buf.size >= self.CHUNK:
            chunk = self._buf[: self.CHUNK]
            self._buf = self._buf[self.CHUNK :]
            x = np.concatenate([self._ctx, chunk])[None, :]
            out, self._state = self._sess.run(
                None, {"input": x, "state": self._state, "sr": self._sr}
            )
            self._ctx = chunk[-self.CONTEXT :].copy()
            self.last_p = float(out[0, 0])

        if self.last_p >= self.open_p:
            self._open = True
            self._hang = self.HANG_FRAMES
        elif self._hang > 0:
            self._hang -= 1
        elif self.last_p < self.close_p:
            self._open = False
        return self._open


class _CallableDetector:
    """Wrap a bare `frame -> bool` callable in the detector interface."""

    def __init__(self, fn: Callable[[bytes], bool], name: str = "callable") -> None:
        self._fn = fn
        self.name = name
        self.last_p = 1.0

    def is_speech(self, frame: bytes) -> bool:
        speech = bool(self._fn(frame))
        self.last_p = 1.0 if speech else 0.0
        return speech


def _as_detector(candidate) -> object:
    """Anything with `is_speech` wins; a plain callable is wrapped."""
    if hasattr(candidate, "is_speech"):
        if not hasattr(candidate, "last_p"):
            candidate.last_p = 1.0
        return candidate
    return _CallableDetector(candidate)


def default_detector() -> object:
    """Best available detector: Silero → webrtcvad → energy gate.

    Each failure is a *log line*, not a silent downgrade — running on the energy
    gate without knowing it is how room tone turns into transcripts.
    """
    try:
        vad = SileroVad()
        _log.info("[voice.session] VAD: silero v5 (onnx)")
        return vad
    except Exception as exc:
        _log.warning("[voice.session] Silero VAD unavailable (%s) — trying webrtcvad", exc)
    try:
        import webrtcvad

        vad = webrtcvad.Vad(2)

        def _webrtc(frame: bytes) -> bool:
            return vad.is_speech(frame, audio.SAMPLE_RATE)

        _log.info("[voice.session] VAD: webrtcvad mode 2")
        return _CallableDetector(_webrtc, name="webrtcvad")
    except Exception as exc:
        _log.warning("[voice.session] webrtcvad unavailable (%s) — falling back to the energy gate", exc)
    return EnergyVad()


def detector_name(detector) -> str:
    """Short label for status/logs ('silero', 'webrtcvad', 'EnergyVad', ...)."""
    return getattr(detector, "name", type(detector).__name__)


def _is_hallucination(text: str, *, voiced: float, peak_p: float) -> bool:
    """True when a decoded final is noise dressed up as an answer.

    Room tone decodes to a handful of stock phrases. They are only dropped when
    the evidence for speech was weak — a quiet but real "yes" still gets through,
    because the VAD was confident about it.
    """
    words = " ".join(
        text.lower().replace(".", " ").replace(",", " ").replace("!", " ").split()
    )
    if not words:
        return True
    stock = words in HALLUCINATIONS or (len(words.split()) == 1 and len(words) <= 3)
    if not stock:
        return False
    return peak_p < BARGE_IN_P or voiced < HALLUCINATION_VOICED


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
        silence_ms_question: int = SILENCE_MS_QUESTION,
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
        self.detector = _as_detector(speech_detector or default_detector())
        self.silence_ms = silence_ms
        self.silence_ms_question = silence_ms_question
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
        self._barge_hits = 0     # consecutive confident speech frames while a turn runs
        self._peak_p = 0.0       # loudest VAD probability in this utterance
        self._last_speech_idx = 0  # frames up to the last voiced frame (excludes the tail)
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
        reset = getattr(self.detector, "reset", None)
        if callable(reset):
            reset()          # a fresh session, not the last one's recurrent state
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
            "vad": detector_name(self.detector),
            "last_p": round(float(getattr(self.detector, "last_p", 0.0)), 3),
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
        speech = bool(self.detector.is_speech(frame))
        prob = float(getattr(self.detector, "last_p", 1.0 if speech else 0.0))

        if not self._in_utterance:
            self._preroll.append(frame)
            if not speech:
                self._barge_hits = 0
                return
            if self._turn is not None:
                # Barge-in is irreversible (it kills the reply), so it needs more
                # evidence than "the VAD opened": a confident frame, sustained.
                if prob < BARGE_IN_P:
                    self._barge_hits = 0
                    return
                self._barge_hits += 1
                if self._barge_hits < BARGE_IN_FRAMES:
                    return
                self._barge_hits = 0
                self._barge_in()
            self._begin_utterance()
            self._peak_p = prob
            return

        self._frames.append(frame)
        self._peak_p = max(self._peak_p, prob)
        self._feed_asr(frame)
        if speech:
            self._speech_ms += FRAME_MS
            self._silence_ms = 0
            self._last_speech_idx = len(self._frames)
        else:
            self._silence_ms += FRAME_MS
        # A drafted question mark means the user finished asking: don't sit on
        # 700 ms of dead air when 500 ms already reads as "done".
        endpoint = (
            self.silence_ms_question
            if self.last_partial.rstrip().endswith("?")
            else self.silence_ms
        )
        if self._silence_ms >= endpoint:
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
        peak_p = self._peak_p
        # Ratio over the spoken part only: the trailing endpoint silence must not
        # make a short, clean answer look like noise (240 ms "no" + 700 ms tail
        # would otherwise score 0.26).
        spoken_ms = max(speech_ms, self._last_speech_idx * FRAME_MS)
        voiced = speech_ms / spoken_ms if spoken_ms else 0.0
        self._reset_utterance()
        if frames_ms == 0 or speech_ms < self.min_speech_ms:
            self._emit({"type": "ignored", "reason": "too_short"})
            self._set_state("listening" if self._listening else "idle")
            return
        if voiced < LOW_VOICED_RATIO:
            # The VAD let a frame or two through, but the window was almost all
            # non-speech: sending it to the ASR only manufactures a transcript.
            _log.info(
                "[voice.session] ignored: mostly noise (voiced=%.2f peak_p=%.2f %.2fs)",
                voiced, peak_p, frames_ms / 1000,
            )
            self._emit({"type": "ignored", "reason": "mostly_noise", "voiced": round(voiced, 2)})
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
        if _is_hallucination(text, voiced=voiced, peak_p=peak_p):
            # Room tone decodes to a small set of stock phrases. Dropping them
            # only when the utterance was thin keeps a real, quiet "yes" alive.
            _log.info(
                "[voice.session] ignored: hallucination %r (voiced=%.2f peak_p=%.2f)",
                text, voiced, peak_p,
            )
            self._emit(
                {"type": "ignored", "reason": "hallucination", "text": text, "voiced": round(voiced, 2)}
            )
            self._set_state("listening" if self._listening else "idle")
            return

        _log.info(
            "[voice.session] utterance %.2fs voiced=%.2f peak_p=%.2f vad=%s text=%r",
            frames_ms / 1000, voiced, peak_p, detector_name(self.detector), text,
        )
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
        self._peak_p = 0.0
        self._last_speech_idx = 0
        self._barge_hits = 0
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
