"""Sentence splitting decides when the orb starts speaking.

The word floor for punctuation breaks was 4, which silently deferred common short
sentences ("First sentence." is two words) to the end of the turn — the reply was
correct and the latency was wrong.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from voice import tts  # noqa: E402


def test_two_word_sentences_are_spoken_as_soon_as_the_next_one_starts():
    stream = tts.SpeechStream()
    assert stream.feed("First sentence. ") == ""      # nothing follows yet
    assert stream.feed("Second") == "First sentence."  # next sentence began


def test_a_bare_acknowledgement_is_still_not_spoken_alone():
    assert tts.sentence_boundary("OK. Sure thing") == ("", "OK. Sure thing")


def test_longer_sentences_still_split_normally():
    ready, rest = tts.sentence_boundary("The capital is Washington. It was built later.")
    assert ready == "The capital is Washington."
    assert rest.strip() == "It was built later."


def test_newline_breaks_still_need_three_words():
    assert tts.sentence_boundary("Done.\nNext line here")[0] == ""
    assert tts.sentence_boundary("All done now.\nNext line here")[0] == "All done now."
