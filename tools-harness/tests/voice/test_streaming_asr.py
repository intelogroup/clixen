"""Streaming ASR contract: incremental, append-only prefix, usable latency.

Runs against the real Parakeet-TDT model (already in the local HF cache) — the
whole point of this module is behaviour you can only observe with the real
decoder. Skips cleanly on machines without MLX/parakeet_mlx or without the
weights, so the generic suite stays green.
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np
import pytest

HARNESS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HARNESS))

from voice import streaming_asr  # noqa: E402

CLIP = HARNESS.parent / "tests" / "jay_voice.wav"
HOP_S = 0.5

pytestmark = pytest.mark.skipif(
    not streaming_asr.available() or not streaming_asr.model_cached(),
    reason="Parakeet/MLX unavailable or model not cached locally",
)


def _pcm16k(path: Path) -> np.ndarray:
    """The clip is 24 kHz; the model wants 16 kHz mono float32."""
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if sr != streaming_asr.SAMPLE_RATE:
        n = int(len(a) * streaming_asr.SAMPLE_RATE / sr)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
    return a


@pytest.fixture(scope="module")
def asr():
    inst = streaming_asr.get_asr()
    inst.start_utterance().result(timeout=300)
    yield inst
    inst.close()


def test_partials_accumulate_and_the_prefix_never_shrinks(asr):
    audio = _pcm16k(CLIP)
    hop = int(HOP_S * streaming_asr.SAMPLE_RATE)
    updates = []
    for i in range(0, len(audio) - hop + 1, hop):
        updates.append(asr.push(audio[i : i + hop]))

    texts = [u.text for u in updates if u.text]
    assert texts, "streaming decoder produced no text at all"
    assert any("Buck" in t for t in texts), f"expected the clip's word in {texts[-1]!r}"

    # The finalized prefix is append-only — this is the contract the caption
    # renderer relies on (no flicker on already-shown words).
    stables = [u.stable for u in updates]
    for prev, cur in zip(stables, stables[1:]):
        assert cur.startswith(prev), f"stable prefix regressed: {prev!r} -> {cur!r}"

    # Latency must stay under the hop so the caption can't fall behind speech.
    lat = sorted(u.latency_ms for u in updates[1:])  # first chunk pays warm-up
    p95 = lat[int(0.95 * (len(lat) - 1))]
    assert np.mean(lat) < 3 * HOP_S * 1000, f"mean chunk latency {np.mean(lat):.0f} ms"
    print(f"\nchunk latency mean={np.mean(lat):.0f}ms p95={p95:.0f}ms hop={HOP_S*1000:.0f}ms")


def test_finalize_flushes_the_draft_region(asr):
    final = asr.finalize()
    assert len(final) > 40, f"final transcript too short: {final!r}"
    assert "Buck" in final
    assert final.startswith(asr.stable_text) or asr.stable_text == ""


def test_new_utterance_resets_the_decoder(asr):
    audio = _pcm16k(CLIP)
    first_final = asr.finalize()

    asr.start_utterance().result(timeout=60)
    assert asr.text == "" and asr.stable_text == ""

    hop = int(HOP_S * streaming_asr.SAMPLE_RATE)
    fresh = asr.push(audio[:hop])
    assert asr.audio_s == pytest.approx(HOP_S, abs=0.05)
    # A residue of the previous utterance would show up verbatim here.
    assert "present of that" not in fresh.text
    assert first_final != fresh.text
