"""The agent contract every voice surface relies on.

All model edges are faked: the point here is turn lifecycle, event order,
barge-in and failure handling — the parts that decide whether talking over the
assistant feels alive or dead.
"""
from __future__ import annotations

import base64
import sys
import threading
import time
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HARNESS))

from voice import agent as agent_mod  # noqa: E402
from voice.agent import VoiceAgent, VoiceEvent  # noqa: E402


def _kinds(events):
    return [e.kind for e in events]


@pytest.fixture
def fast_tts(monkeypatch):
    monkeypatch.setattr(agent_mod.stt, "transcribe_bytes", lambda data, suffix=".webm", language="": "hello there")
    monkeypatch.setattr(agent_mod.stt, "is_error", lambda text: not text or text.startswith("err:"))
    monkeypatch.setattr(agent_mod.tts, "synthesize_cached", lambda text, voice: b"WAV")
    return monkeypatch


def _brain(*tokens, reply="all done", delay=0.0):
    def brain(query, *, chat_id, on_token, turn_id, force_web_search):
        for token in tokens or (reply,):
            if delay:
                time.sleep(delay)
            on_token(token)
        return (reply, "fake-model", "chat")

    return brain


def test_turn_streams_transcript_tokens_audio_then_done(fast_tts):
    agent = VoiceAgent()
    agent.set_brain(_brain("Hello. ", "There. "))
    events = list(agent.stream_turn(audio=b"fake", chat_id="voice_agent"))
    kinds = _kinds(events)
    assert kinds[0] == "transcript"
    assert events[0].text == "hello there"
    assert "token" in kinds and "audio" in kinds
    assert kinds[-1] == "done"
    assert events[-1].model == "fake-model"
    # one audio clip per completed sentence ("Hello." / "There." / flush "")
    assert kinds.count("audio") >= 1
    assert all(e.turn_id == events[0].turn_id for e in events)


def test_stt_failure_becomes_an_error_and_never_reaches_the_brain():
    called = []
    agent = VoiceAgent()
    agent.set_brain(lambda *a, **k: called.append(1))
    original = agent_mod.stt.transcribe_bytes
    agent_mod.stt.transcribe_bytes = lambda *a, **k: "err: whisper produced no output"
    try:
        events = list(agent.stream_turn(audio=b"junk"))
    finally:
        agent_mod.stt.transcribe_bytes = original
    assert _kinds(events) == ["error"]
    assert not called


def test_tts_outage_still_delivers_text_and_done(fast_tts):
    fast_tts.setattr(agent_mod.tts, "synthesize_cached", lambda text, voice: b"")
    agent = VoiceAgent()
    agent.set_brain(_brain("One. ", "Two. "))
    events = list(agent.stream_turn(audio=b"fake"))
    assert "audio" not in _kinds(events)
    assert _kinds(events)[-1] == "done"


def test_tts_disabled_skips_synthesis(fast_tts):
    calls = []
    fast_tts.setattr(agent_mod.tts, "synthesize_cached", lambda text, voice: calls.append(text) or b"WAV")
    agent = VoiceAgent()
    agent.set_brain(_brain("One. ", "Two. "))
    list(agent.stream_turn(audio=b"fake", tts_enabled=False))
    assert calls == []


def test_no_brain_is_refused():
    with pytest.raises(RuntimeError):
        VoiceAgent().begin_turn(query="hi")


def test_brain_exception_is_an_error_event(fast_tts):
    def brain(*a, **k):
        raise RuntimeError("model exploded")

    agent = VoiceAgent()
    agent.set_brain(brain)
    events = list(agent.stream_turn(query="hi"))
    assert _kinds(events)[-1] == "error"
    assert "model exploded" in events[-1].text


def test_voice_event_serialises_audio_as_base64():
    ev = VoiceEvent("audio", data=b"RIFFxx", turn_id="t1", model="m")
    d = ev.as_dict()
    assert d["type"] == "audio" and d["turn_id"] == "t1" and d["model"] == "m"
    assert base64.b64decode(d["data"]) == b"RIFFxx"


def test_token_filter_is_applied(fast_tts):
    agent = VoiceAgent()
    agent.set_brain(_brain("keep ", "DROP ", "this. "))
    events = list(
        agent.stream_turn(
            query="hi",
            tts_enabled=False,
            token_filter=(lambda t: "" if t.strip() == "DROP" else t, lambda: ""),
        )
    )
    text = "".join(e.text for e in events if e.kind == "token")
    assert "DROP" not in text and "keep" in text


def test_speak_events_one_audio_per_sentence(fast_tts):
    fast_tts.setattr(agent_mod.tts, "iter_wavs", lambda text, voice: iter([b"a", b"b"]))
    events = list(VoiceAgent().speak_events("One. Two."))
    assert _kinds(events) == ["audio", "audio", "done"]


# ── barge-in / cancellation ────────────────────────────────────────────────


def _blocking_brain():
    """Stands in for the LLM loop: waits on the thread's abort_event, then unwinds."""

    def brain(query, *, chat_id, on_token, turn_id, force_web_search):
        from clients.cancellation import QueryAbortedException

        event = getattr(threading.current_thread(), "abort_event", None)
        assert event is not None, "brain thread must carry abort_event"
        if not event.wait(5.0):
            raise AssertionError("turn was never aborted")
        raise QueryAbortedException("aborted")

    return brain


def test_cancel_emits_cancelled_and_frees_the_chat(fast_tts):
    agent = VoiceAgent()
    agent.set_brain(_blocking_brain())
    seen = []
    worker = threading.Thread(target=lambda: seen.extend(agent.stream_turn(query="hi")), daemon=True)
    worker.start()
    time.sleep(0.2)
    assert agent.is_busy()
    assert agent.cancel() is True
    worker.join(5)
    assert _kinds(seen) == ["cancelled"]
    assert not agent.is_busy()
    assert agent.cancel() is False


def test_a_newer_turn_barges_in_and_cancels_the_running_one(fast_tts):
    agent = VoiceAgent()
    agent.set_brain(_blocking_brain())
    seen = []
    worker = threading.Thread(target=lambda: seen.extend(agent.stream_turn(query="first")), daemon=True)
    worker.start()
    time.sleep(0.2)
    first_id = agent.active_turn()
    second = agent.begin_turn(query="second")
    worker.join(5)
    assert _kinds(seen) == ["cancelled"]
    assert second.turn_id != first_id
    assert agent.active_turn() == second.turn_id
    second.cancel()


def test_brain_timeout_is_reported_as_an_error(fast_tts):
    def slow_brain(*_a, on_token, **_k):
        time.sleep(0.8)
        on_token("late")

    agent = VoiceAgent(timeout=0.15, filler_after_s=0, filler_texts=())
    agent.set_brain(slow_brain)
    events = list(agent.stream_turn(query="hi", tts_enabled=False))
    assert _kinds(events) == ["error"]
    assert "timed out" in events[0].text


def test_slow_brain_gets_a_filler_before_the_reply(fast_tts):
    """The line must not be silent while the orchestrator thinks."""

    def slow_brain(*_a, on_token, **_k):
        time.sleep(0.5)
        on_token("Here is the answer. ")
        return ("Here is the answer.", "fake-model", "chat")

    agent = VoiceAgent(timeout=5, filler_after_s=0.1, filler_texts=("Let me check.",))
    # Prime synchronously: in production this runs in the background at first turn.
    agent._filler_wavs["Let me check."] = b"FILLERWAV"
    agent._filler_primed = True
    agent.set_brain(slow_brain)

    events = list(agent.stream_turn(query="hi"))
    kinds = _kinds(events)
    assert kinds[0] == "audio" and events[0].data == b"FILLERWAV"
    assert kinds.index("token") > 0          # filler came first
    assert kinds[-1] == "done"
    assert kinds.count("audio") == 2         # filler + the real sentence


# ── async + fan-out ────────────────────────────────────────────────────────


def test_async_stream_turn(fast_tts):
    import asyncio

    agent = VoiceAgent()
    agent.set_brain(_brain("Hi. "))

    async def collect():
        return [e.kind async for e in agent.astream_turn(query="hi")]

    kinds = asyncio.run(collect())
    assert kinds[0] == "token" and kinds[-1] == "done"


def test_subscribers_receive_published_events():
    agent = VoiceAgent()
    q = agent.subscribe()
    agent.publish(VoiceEvent("partial", text="hey"))
    ev = q.get_nowait()
    assert ev.kind == "partial" and ev.text == "hey"
    agent.unsubscribe(q)
    agent.publish(VoiceEvent("token", text="ignored"))
    assert q.empty()


def test_audio_starts_before_the_reply_finishes_streaming(fast_tts):
    """Latency guard (A1): the first clip is synthesised as soon as the first sentence
    is complete, not when the last token arrives.

    This is the property that broke before: a short reply never crossed the leak
    filter's holdback, so the whole turn was buffered and the user heard nothing for
    ~2.5 s, then everything at once. Deterministic here rather than a p50 measurement:
    the brain sleeps between tokens, so "audio before the end" is a binary fact.
    """
    agent = VoiceAgent()

    def brain(query, *, chat_id, on_token, turn_id, force_web_search):
        # The split happens once the NEXT sentence has begun (the splitter looks ahead
        # for the capital), so the second token is what completes the first sentence.
        on_token("First sentence. ")
        on_token("Second")
        time.sleep(0.35)          # a buffered turn would miss this deadline
        on_token(" one.")
        return ("First sentence. Second one.", "fake-model", "chat")

    agent.set_brain(brain)
    started = time.time()
    first_audio = None
    kinds = []
    for event in agent.stream_turn(audio=b"fake", chat_id="voice_agent"):
        kinds.append(event.kind)
        if event.kind == "audio" and first_audio is None:
            first_audio = time.time() - started

    assert first_audio is not None, kinds
    assert first_audio < 0.35, f"first audio at {first_audio:.2f}s — the turn was buffered"
    assert kinds.count("audio") >= 1 and kinds[-1] == "done"
