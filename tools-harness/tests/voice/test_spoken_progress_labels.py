"""Tool-progress labels must be shown, never spoken.

cloud_client pushes "⚙️ Running observe…" onto the same on_token channel the
caption renders. tts.strip_for_speech removes the emoji but keeps the words, so
the orb said "Running observe… Ghostty is in front." before every tool-using
answer (confirmed live 2026-10-24).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from voice.agent import strip_progress_labels, _PROGRESS_LABEL_RE  # noqa: E402
from voice import tts  # noqa: E402


def test_progress_labels_are_not_spoken():
    for label in (
        "\n\n⚙️ Running observe…",
        "\n\n🔍 Searching the web…",
        "\n\n📄 Reading file…",
        "\n\n🌐 Opening browser…",
    ):
        assert strip_progress_labels(label) == "", label


def test_normal_reply_text_is_untouched():
    for text in ("Ghostty is in front.", "Four.", "\n\nThe answer is 4."):
        assert strip_progress_labels(text) == text


def test_the_label_and_answer_in_one_chunk_keeps_only_the_answer():
    """Observed live: the label and the first content delta arrived concatenated."""
    assert strip_progress_labels("\n\n⚙️ Running observe…Ghostty is in front.") == "Ghostty is in front."


def test_the_label_would_have_been_spoken_without_the_filter():
    """Proof the filter is needed, not cosmetic: strip_for_speech keeps the words."""
    label_and_answer = "\n\n⚙️ Running observe…Ghostty is in front."
    assert "Running observe" in tts.strip_for_speech(label_and_answer)
    assert strip_progress_labels(label_and_answer) != ""
    # ...and the label itself is what gets dropped, not the answer.
    assert strip_progress_labels("\n\n⚙️ Running observe…") == ""


def test_a_sentence_containing_an_ellipsis_is_not_treated_as_a_label():
    assert strip_progress_labels("Well… I think so.") == "Well… I think so."


def test_the_pattern_is_anchored_to_a_leading_emoji():
    assert _PROGRESS_LABEL_RE.match("Running observe…") is None


def test_a_whole_turn_never_synthesizes_the_label(monkeypatch):
    """Integration: the label reaches the caption but never the TTS input."""
    from voice import agent as agent_mod

    monkeypatch.setattr(agent_mod.stt, "transcribe_bytes",
                        lambda data, suffix=".webm", language="": "what is in front")
    monkeypatch.setattr(agent_mod.stt, "is_error", lambda text: not text)
    spoken: list[str] = []

    def fake_tts(text, voice):
        spoken.append(text)
        return b"WAV"

    monkeypatch.setattr(agent_mod.tts, "synthesize_cached", fake_tts)

    def brain(query, *, chat_id, on_token, turn_id, force_web_search):
        on_token("\n\n⚙️ Running observe…")
        on_token("Ghostty is in front.")
        return ("Ghostty is in front.", "fake-model", "chat")

    agent = agent_mod.VoiceAgent(filler_after_s=0.0)
    agent.set_brain(brain)
    events = list(agent.stream_turn(audio=b"fake", chat_id="voice_agent"))

    assert any("Running observe" in e.text for e in events if e.kind == "token"), \
        "the caption still needs the progress label"
    assert spoken, "the turn should have spoken something"
    assert not any("Running observe" in text for text in spoken), spoken
    assert any("Ghostty is in front" in text for text in spoken), spoken
