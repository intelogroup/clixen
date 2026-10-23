"""Continuous-listening session: toggle on, many turns, barge-in, toggle off.

The mic and the ASR are faked (deterministic frames, a stub decoder). What is
under test is the thing the user actually feels: does the mic stay hot across
turns until they switch it off, does a short blip not become a question, does
talking over the reply cut it, and does the caption get partials while speaking.
"""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

HARNESS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HARNESS))

from voice import session as session_mod  # noqa: E402
from voice.agent import VoiceAgent  # noqa: E402
from voice.session import EnergyVad, FRAME_BYTES, VoiceSession  # noqa: E402

SILENCE = b"\x00" * FRAME_BYTES
SPEECH = np.full(FRAME_BYTES // 2, 6000, dtype="<i2").tobytes()


class FakeASR:
    def __init__(self, text: str = "what time is it") -> None:
        self.text = text
        self.starts = 0
        self.pushes = 0
        self.finalized = 0

    def start_utterance(self) -> Future:
        self.starts += 1
        fut = Future()
        fut.set_result(None)
        return fut

    def push(self, chunk) -> SimpleNamespace:
        self.pushes += 1
        return SimpleNamespace(text=self.text, stable=self.text, audio_s=0.0, latency_ms=0.0)

    def finalize(self) -> str:
        self.finalized += 1
        return self.text


def _brain(*tokens: str):
    def run(query, *, chat_id, on_token, turn_id, force_web_search):
        for token in tokens or ("Done. ",):
            on_token(token)
        return ("Done.", "fake-model", "chat")

    return run


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


def _utterance(session, *, speech_frames: int = 6, tail_frames: int = 8) -> None:
    for _ in range(speech_frames):
        session.feed_pcm(SPEECH)
    for _ in range(tail_frames):
        session.feed_pcm(SILENCE)


@pytest.fixture
def events():
    return []


@pytest.fixture
def make_session(monkeypatch, events):
    from voice import agent as agent_mod

    monkeypatch.setattr(agent_mod.tts, "synthesize_cached", lambda text, voice: b"WAV")
    monkeypatch.setattr(session_mod.stt, "is_error", lambda text: not text or text.startswith("err:"))

    def build(*, asr=None, brain=None, **kwargs) -> VoiceSession:
        agent = VoiceAgent()
        agent.set_brain(brain or _brain())
        return VoiceSession(
            agent=agent,
            asr=asr or FakeASR(),
            on_event=events.append,
            # non-silent frame == speech: deterministic, no audio hardware
            speech_detector=lambda frame: frame != SILENCE,
            silence_ms=150,
            min_speech_ms=90,
            **kwargs,
        )

    return build


def test_toggle_on_listens_across_turns_until_switched_off(make_session, events):
    session = make_session()
    assert session.start(mic=False) is True
    assert session.is_listening

    _utterance(session)
    assert _wait_for(lambda: any(e.get("type") == "final" for e in events))
    assert _wait_for(lambda: any(e.get("type") == "done" for e in events))

    # Still listening: a second question needs no re-arm.
    _utterance(session)
    assert _wait_for(lambda: sum(e.get("type") == "final" for e in events) == 2)
    assert session.is_listening and session.turns == 2
    assert _wait_for(lambda: session.state == "listening")

    session.stop()
    assert not session.is_listening and session.state == "idle"
    before = len(events)
    _utterance(session)
    time.sleep(0.5)
    assert len(events) == before, "a stopped session must not transcribe anything"


def test_short_blip_is_not_a_question(make_session, events):
    asr = FakeASR()
    session = make_session(asr=asr)
    session.start(mic=False)
    _utterance(session, speech_frames=1)   # 30 ms — a click, not a word
    assert _wait_for(lambda: any(e.get("type") == "ignored" for e in events))
    assert not any(e.get("type") == "final" for e in events)
    assert asr.finalized == 0
    session.stop()


def test_partials_arrive_while_speaking(make_session, events):
    session = make_session(partial_every_ms=500)
    session.start(mic=False)
    _utterance(session, speech_frames=24)   # 720 ms of speech → one ASR push
    assert _wait_for(lambda: any(e.get("type") == "partial" for e in events))
    partial = [e for e in events if e.get("type") == "partial"][0]
    assert partial["text"] == "what time is it"
    session.stop()


def test_transcription_failure_surfaces_as_error_and_skips_the_brain(make_session, events):
    calls = []
    session = make_session(asr=FakeASR(text="err: no speech"))
    session.agent.set_brain(lambda *a, **k: calls.append(1))
    session.start(mic=False)
    _utterance(session)
    assert _wait_for(lambda: any(e.get("type") == "error" for e in events))
    assert calls == [] and session.turns == 0
    session.stop()


def test_talking_over_a_reply_barges_in(make_session, events):
    session = make_session(brain=_blocking_brain())
    session.start(mic=False)

    _utterance(session)
    assert _wait_for(lambda: any(e.get("type") == "final" for e in events))
    assert _wait_for(lambda: session.state == "thinking")

    _utterance(session)   # user speaks again while the turn is running
    assert _wait_for(lambda: any(e.get("type") == "interrupt" for e in events))
    assert _wait_for(lambda: any(e.get("type") == "cancelled" for e in events))
    session.stop()


def test_ragged_frame_tail_is_dropped_not_misaligned(make_session):
    session = make_session()
    session.start(mic=False)
    session.feed_pcm(SPEECH + b"\x01\x02\x03")   # 3 stray bytes must be ignored
    session.feed_pcm(SPEECH)
    time.sleep(0.2)
    assert session.is_listening
    session.stop()
    assert session.state == "idle"


def test_energy_vad_opens_and_closes():
    vad = EnergyVad(min_rms=0.004, ratio=2.0, onset_frames=1, hang_frames=2)
    loud = np.full(FRAME_BYTES // 2, 12000, dtype="<i2").tobytes()
    quiet = b"\x00" * FRAME_BYTES
    assert vad(quiet) is False
    assert vad(loud) is True
    assert vad(quiet) is True     # hangover keeps the utterance open
    vad(quiet)
    assert vad(quiet) is False    # closes after the hangover
