"""clixen_ptt transcription: Parakeet on in-memory audio, English and French.

Speech is synthesized with macOS `say`, so this only runs on a Mac with the
Parakeet weights already in the Hugging Face cache (no 2 GB download in tests).
"""
import shutil
import subprocess
import sys

import pytest
import soundfile as sf

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("say"),
                                reason="needs macOS `say`")


@pytest.fixture(scope="module")
def ptt():
    import clixen_ptt
    from huggingface_hub import try_to_load_from_cache

    if not isinstance(try_to_load_from_cache(clixen_ptt.PARAKEET_MODEL, "config.json"), str):
        pytest.skip("Parakeet weights not cached")
    return clixen_ptt


def _say(tmp_path, voice, text):
    wav = tmp_path / f"{voice}.wav"
    subprocess.run(["say", "-v", voice, "--data-format=LEF32@16000", "-o", str(wav), text], check=True)
    audio, sr = sf.read(str(wav), dtype="float32")
    assert sr == 16000
    return audio.reshape(-1, 1)  # sounddevice delivers (frames, channels)


@pytest.mark.parametrize("voice,text,expect", [
    ("Samantha", "Please send the invoice to my accountant tomorrow morning.", {"invoice", "accountant", "tomorrow"}),
    ("Thomas", "Je voudrais changer mon adresse, s'il vous plaît.", {"changer", "adresse"}),
])
def test_transcribe_array(ptt, tmp_path, voice, text, expect):
    out = ptt.transcribe_array(_say(tmp_path, voice, text)).lower()
    assert expect <= set(out.replace(",", " ").replace(".", " ").split()), out


def test_transcribe_from_short_lived_threads(ptt, tmp_path):
    # Production shape: warm-up at startup, then each key release transcribes
    # from pynput's listener thread. MLX streams die with the thread that made
    # them, which used to raise "There is no Stream(cpu, 1) in current thread".
    import threading

    ptt._mlx_thread.submit(lambda: setattr(ptt, "_model", None)).result()
    ptt.warm_model().result()  # warm-up finishes before the first key release
    audio = _say(tmp_path, "Samantha", "Testing one two three.")
    out = []

    def listener():
        try:
            out.append(ptt.transcribe_array(audio))
        except Exception as e:  # surfaces in the assert below, not as a thread warning
            out.append(f"ERR {e}")

    for _ in range(2):  # the first caller thread exits before the second starts
        t = threading.Thread(target=listener)
        t.start()
        t.join()
    assert all("three" in o.lower() for o in out), out


def test_transcribe_array_silence_is_empty(ptt):
    import numpy as np

    assert ptt.transcribe_array(np.zeros((16000, 1), dtype=np.float32)) == ""
