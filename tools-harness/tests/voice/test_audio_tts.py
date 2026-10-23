"""Audio + TTS contracts the session layer depends on.

No device, no network, no model: every external edge (ffmpeg output, Kokoro
daemon, the OS player) is either exercised for real where it is cheap or
monkeypatched where it would make noise.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

HARNESS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HARNESS))

from voice import audio, tts  # noqa: E402


# ── audio ──────────────────────────────────────────────────────────────────


def _tone(seconds=0.5, rate=24000, hz=440.0):
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def test_wav_roundtrip_keeps_rate_and_samples():
    samples = _tone(0.25)
    wav = audio.wav_bytes(samples, 24000)
    assert wav[:4] == b"RIFF"
    back, rate = audio.wav_to_float(wav)
    assert rate == 24000
    assert len(back) == len(samples)
    assert np.max(np.abs(back - samples)) < 1e-3


def test_pcm16_float_roundtrip_is_lossless_enough():
    samples = _tone(0.1, rate=16000)
    pcm = audio.float_to_pcm16(samples)
    assert len(pcm) == len(samples) * 2
    back = audio.pcm16_to_float(pcm)
    assert np.max(np.abs(back - samples)) < 1e-3


def test_resample_hits_the_target_length_and_keeps_the_peak():
    src = _tone(0.5, rate=24000)
    out = audio.resample(src, 24000, 16000)
    assert abs(len(out) - int(0.5 * 16000)) <= 1
    assert 0.25 < np.max(np.abs(out)) < 0.35


def test_frame_chunks_are_exactly_30ms_and_drop_the_tail():
    samples = np.zeros(16000, dtype=np.float32)
    frames = list(audio.frame_chunks(samples, 480))
    assert len(frames) == 16000 // 480      # 33 frames, 160 samples dropped
    assert all(len(f) == 480 for f in frames)


@pytest.mark.skipif(not audio.have_ffmpeg(), reason="ffmpeg not installed")
def test_decode_to_float_reads_a_container_ffmpeg_understands(tmp_path):
    path = tmp_path / "clip.wav"
    path.write_bytes(audio.wav_bytes(_tone(0.5, rate=24000), 24000))
    out = audio.decode_to_float(path.read_bytes(), suffix=".wav")
    assert abs(len(out) - 8000) <= 400, f"expected ~0.5 s @16k, got {len(out)} samples"
    assert np.max(np.abs(out)) > 0.1


def test_playback_returns_false_when_no_player_exists(monkeypatch):
    monkeypatch.setattr(audio, "_player_cmd", lambda _p: None)
    assert audio.play_wav_bytes(b"RIFFjunk") is False
    assert audio.play_file("/tmp/nope.wav") is False


def test_non_blocking_playback_can_be_stopped(monkeypatch):
    monkeypatch.setattr(audio, "_player_cmd", lambda _p: ["sleep", "30"])
    wav = b"RIFF" + b"\0" * 64
    assert audio.play_wav_bytes(wav, blocking=False) is True
    assert audio._player is not None
    audio.stop_playback()
    assert audio._player is None
    assert audio.play_wav_bytes(b"", blocking=True) is False


def test_blocking_playback_waits_for_the_player(monkeypatch):
    monkeypatch.setattr(audio, "_player_cmd", lambda _p: ["sleep", "0.05"])
    assert audio.play_wav_bytes(b"RIFF" + b"\0" * 32, blocking=True) is True
    assert audio._player is None


# ── tts text preparation ───────────────────────────────────────────────────


def test_strip_for_speech_removes_markdown_and_emoji_but_keeps_words():
    clean = tts.strip_for_speech("**Done** — `file.txt` written 🌡️ ✅")
    assert "*" not in clean and "`" not in clean
    assert "🌡️" not in clean and "✅" not in clean
    assert "Done" in clean and "file.txt" in clean


def test_strip_for_speech_removes_tool_progress_labels_not_content_emoji():
    from clients.cloud_client import _TOOL_PROGRESS_LABELS

    label = _TOOL_PROGRESS_LABELS["ask_web_search"]
    out = tts.strip_for_speech(f"{label}\nHere is the answer.")
    assert "Searching the web" not in out
    assert out.strip() == "Here is the answer."
    # A weather reply's own words survive; only the glyph is stripped.
    assert "Condition: sunny" in tts.strip_for_speech("☁️ Condition: sunny")


def test_split_sentences():
    assert tts.split_sentences("One thing. Two things! Three things?") == [
        "One thing.",
        "Two things!",
        "Three things?",
    ]
    assert tts.split_sentences("   ") == []


def test_wrap_for_kokoro_never_exceeds_the_phoneme_guard():
    text = " ".join(["word"] * 300)  # ~1499 chars
    pieces = tts.wrap_for_kokoro(text)
    assert len(pieces) > 1
    assert all(len(p) <= tts.KOKORO_MAX_CHARS for p in pieces)
    assert " ".join(pieces) == text


def test_sentence_boundary_waits_for_enough_words():
    assert tts.sentence_boundary("Just two words.") == ("", "Just two words.")
    ready, rest = tts.sentence_boundary("I opened the file. Next thing")
    assert ready == "I opened the file."
    assert rest.strip() == "Next thing"
    ready, rest = tts.sentence_boundary("Done\n\nstill going")
    assert ready == "" and rest == "Done\n\nstill going"


# ── tts synthesis fallbacks ────────────────────────────────────────────────


def test_synthesize_passes_through_daemon_wav(monkeypatch):
    monkeypatch.setattr(tts, "wrap_for_kokoro", lambda text, max_chars=300: [text])
    monkeypatch.setattr(tts, "_daemon_synthesize", lambda text, voice: b"RIFFdaemon")
    assert tts.synthesize_wav("Hello there.") == b"RIFFdaemon"


def test_synthesize_degrades_to_empty_when_every_engine_is_down(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("down")

    monkeypatch.setattr(tts, "_daemon_synthesize", boom)
    monkeypatch.setattr(tts, "_local_synthesize", boom)
    assert tts.synthesize_wav("Hello there.") == b""


def test_synthesize_empty_text_is_a_noop():
    assert tts.synthesize_wav("   ") == b""


def test_iter_wavs_is_one_clip_per_sentence(monkeypatch):
    monkeypatch.setattr(tts, "synthesize_cached", lambda s, v: f"wav:{s}".encode())
    assert list(tts.iter_wavs("First one. Second one!")) == [b"wav:First one.", b"wav:Second one!"]


def test_speak_returns_false_without_tts(monkeypatch):
    monkeypatch.setattr(tts, "synthesize_wav", lambda *a: b"")
    monkeypatch.setattr(tts.sys, "platform", "linux")
    assert tts.speak("nothing to say") is False
