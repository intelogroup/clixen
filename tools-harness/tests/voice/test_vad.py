"""VAD quality + turn-taking guards.

The bug this file pins down: with the energy gate, room tone counted as speech,
so the mic stayed "understanding" whatever the fan was doing and the ASR
invented answers ("Yeah.", "Okay."). These tests run the real Silero model on
real synthesized speech and on white noise, then check the guards that stop a
noise-shaped final or a timid barge-in.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

HARNESS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HARNESS))

from voice import session as session_mod  # noqa: E402
from voice.agent import VoiceAgent  # noqa: E402
from voice.session import (  # noqa: E402
    FRAME_BYTES,
    FRAME_SAMPLES,
    SileroVad,
    VoiceSession,
)

SILENCE = b"\x00" * FRAME_BYTES
SPEECH = np.full(FRAME_SAMPLES, 6000, dtype="<i2").tobytes()


def _tts_pcm(text: str) -> np.ndarray | None:
    """16 kHz mono float32 speech, synthesized with the system voice."""
    if not (shutil.which("say") and shutil.which("afconvert")):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = Path(tmp) / "s.aiff", Path(tmp) / "s.wav"
        subprocess.run(["say", "-v", "Samantha", "-o", str(aiff), text], check=True, capture_output=True)
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
            check=True, capture_output=True,
        )
        with wave.open(str(wav)) as handle:
            raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _frames(pcm: np.ndarray) -> list[bytes]:
    return [
        (np.clip(pcm[i : i + FRAME_SAMPLES], -1, 1) * 32768).astype("<i2").tobytes()
        for i in range(0, len(pcm) - FRAME_SAMPLES + 1, FRAME_SAMPLES)
    ]


def _noise(seconds: float, amp: float = 0.02, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(16000 * seconds)) * amp).astype(np.float32)


class StubVad:
    """Detector with a controllable probability — for the guard-rail tests."""

    name = "stub"
    last_p = 0.0

    def __init__(self, p: float = 0.9) -> None:
        self.p = p
        self.last_p = 0.0

    def is_speech(self, frame: bytes) -> bool:
        speech = frame != SILENCE
        self.last_p = self.p if speech else 0.0
        return speech and self.p >= 0.5


class FakeASR:
    def __init__(self, text: str = "what time is it") -> None:
        self.text = text
        self.finalized = 0

    def start_utterance(self) -> Future:
        fut = Future()
        fut.set_result(None)
        return fut

    def push(self, chunk) -> SimpleNamespace:
        return SimpleNamespace(text=self.text, stable=self.text, audio_s=0.0, latency_ms=0.0)

    def finalize(self) -> str:
        self.finalized += 1
        return self.text


def _blocking_brain():
    def run(query, *, chat_id, on_token, turn_id, force_web_search):
        from clients.cancellation import QueryAbortedException

        event = getattr(threading.current_thread(), "abort_event", None)
        assert event is not None and event.wait(5.0)
        raise QueryAbortedException("aborted")

    return run


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def build(monkeypatch):
    from voice import agent as agent_mod

    monkeypatch.setattr(agent_mod.tts, "synthesize_cached", lambda text, voice: b"WAV")
    monkeypatch.setattr(session_mod.stt, "is_error", lambda text: not text or text.startswith("err:"))

    def make(events, *, detector=None, asr=None, brain=None, **kwargs) -> VoiceSession:
        agent = VoiceAgent()
        agent.set_brain(brain or (lambda *a, **k: ("Done.", "fake", "chat")))
        options = dict(silence_ms=150, min_speech_ms=90)
        options.update(kwargs)
        return VoiceSession(
            agent=agent,
            asr=asr or FakeASR(),
            on_event=events.append,
            speech_detector=detector if detector is not None else StubVad(),
            **options,
        )

    return make


def _utterance(session, *, speech_frames: int = 6, tail_frames: int = 8) -> None:
    for _ in range(speech_frames):
        session.feed_pcm(SPEECH)
    for _ in range(tail_frames):
        session.feed_pcm(SILENCE)


# ── the detector itself ────────────────────────────────────────────────────


def test_silero_scores_speech_and_rejects_noise():
    pcm = _tts_pcm("Clixen, please check the weather in Paris and tell me if I need an umbrella.")
    if pcm is None:
        pytest.skip("system TTS (say/afconvert) unavailable")

    vad = SileroVad()
    speech_p = []
    for frame in _frames(pcm):
        vad.is_speech(frame)
        speech_p.append(vad.last_p)
    assert np.mean(speech_p) > 0.6, f"speech should read as speech, got {np.mean(speech_p):.2f}"

    vad.reset()
    noise_p, opened = [], []
    for frame in _frames(_noise(2.0)):
        opened.append(vad.is_speech(frame))
        noise_p.append(vad.last_p)
    assert np.mean(noise_p) < 0.2, f"white noise should not look like speech, got {np.mean(noise_p):.2f}"
    assert not any(opened), "the VAD must never open on room noise"


def test_room_noise_never_becomes_an_utterance(build):
    events: list[dict] = []
    session = build(events, detector=SileroVad())
    session.start(mic=False)
    for frame in _frames(_noise(2.0)):
        session.feed_pcm(frame)
    time.sleep(0.4)
    assert not any(e.get("type") == "final" for e in events)
    assert session.turns == 0
    assert session.state == "listening"
    session.stop()


def test_speech_after_noise_is_transcribed_once(build):
    pcm = _tts_pcm("What is the tracking number for my package?")
    if pcm is None:
        pytest.skip("system TTS (say/afconvert) unavailable")

    events: list[dict] = []
    session = build(events, detector=SileroVad())
    session.start(mic=False)
    for frame in _frames(_noise(1.0)):
        session.feed_pcm(frame)
    for frame in _frames(pcm):
        session.feed_pcm(frame)
    for _ in range(20):
        session.feed_pcm(SILENCE)



# ── guards ────────────────────────────────────────────────────────────────


def test_noise_shaped_stock_phrase_is_dropped(build):
    events: list[dict] = []
    session = build(events, detector=StubVad(p=0.55), asr=FakeASR(text="Yeah."))
    session.start(mic=False)
    _utterance(session)
    assert _wait_for(lambda: any(e.get("type") == "ignored" for e in events))
    ignored = [e for e in events if e.get("type") == "ignored"][-1]
    assert ignored["reason"] == "hallucination"
    assert session.turns == 0
    session.stop()


def test_confident_short_answer_is_kept(build):
    events: list[dict] = []
    session = build(events, detector=StubVad(p=0.95), asr=FakeASR(text="Yes."))
    session.start(mic=False)
    _utterance(session)
    assert _wait_for(lambda: any(e.get("type") == "final" for e in events))
    assert session.turns == 1
    session.stop()


def test_gappy_detection_is_not_a_turn(build):
    events: list[dict] = []
    session = build(events, detector=StubVad(p=0.9), silence_ms=300, min_speech_ms=60)
    session.start(mic=False)
    # One voiced frame in five: the VAD is flickering on something, not on a voice.
    for _ in range(10):
        session.feed_pcm(SPEECH)
        for _ in range(4):
            session.feed_pcm(SILENCE)
    for _ in range(10):          # enough tail to actually close the utterance
        session.feed_pcm(SILENCE)
    assert _wait_for(lambda: any(e.get("type") == "ignored" for e in events))
    assert [e for e in events if e.get("type") == "ignored"][-1]["reason"] == "mostly_noise"
    assert session.turns == 0
    session.stop()


def test_timid_speech_does_not_barge_in(build):
    events: list[dict] = []
    detector = StubVad(p=0.95)
    session = build(events, detector=detector, brain=_blocking_brain())
    session.start(mic=False)

    _utterance(session)
    assert _wait_for(lambda: any(e.get("type") == "final" for e in events))
    assert _wait_for(lambda: session.state == "thinking")

    detector.p = 0.6          # the VAD opens, but not confidently
    for _ in range(20):
        session.feed_pcm(SPEECH)
    time.sleep(0.2)
    assert not any(e.get("type") == "interrupt" for e in events)

    detector.p = 0.95         # real speech: interrupt
    for _ in range(8):
        session.feed_pcm(SPEECH)
    assert _wait_for(lambda: any(e.get("type") == "interrupt" for e in events))
    session.stop()

    assert _wait_for(lambda: any(e.get("type") == "final" for e in events))
    assert sum(e.get("type") == "final" for e in events) == 1, "noise must not add a second turn"
    session.stop()

