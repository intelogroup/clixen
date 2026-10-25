"""analyze_image: understanding, and what happens when the vision tier is down.

Every model call is faked — these pin the contract (what is sent, what comes back,
how it fails), not the model's judgement. The live measurement lives in the commit
message: 1.4 s on a real screen, describing a mind map OCR could not see.
"""
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import vision_analyze as va  # noqa: E402


def _unused_inject(monkeypatch, fake):
    """Install a fake cloud client in a way that survives other suites importing the
    real one first: patching sys.modules alone is not enough once `clients` has
    already been imported, because `from clients import cloud_client` then resolves
    the package attribute, not the module entry. (Found by running this file after
    the harness-booting suites: all seven passed alone, three failed together.)
    """
    import clients

    monkeypatch.setattr(va, "_cloud", lambda: fake)
    monkeypatch.setattr(clients, "cloud_client", fake, raising=False)


class FakeCloud:
    CLOUD_VISION_MODEL = "fake/vision"

    def __init__(self, reply="a payment confirmation dialog", fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    def chat(self, question, **kw):
        self.calls.append({"question": question, **kw})
        if self.fail:
            raise RuntimeError("vision tier unreachable")
        return self.reply


@pytest.fixture
def image(tmp_path):
    """A noisy screenshot-sized PNG: noise is incompressible, so this is a realistic
    worst case for payload size (a flat-colour image compresses to a few KB and would
    make the downscale test meaningless)."""
    from PIL import Image
    import os

    p = tmp_path / "shot.png"
    im = Image.frombytes("RGB", (2400, 1600), os.urandom(2400 * 1600 * 3))
    im.save(p)
    return p


def test_a_question_about_an_image_returns_the_model_answer(image, monkeypatch):
    fake = FakeCloud()
    monkeypatch.setattr(va, "_cloud", lambda: fake)
    out = va.analyze_image("what kind of dialog is this?", path=str(image))
    assert "payment confirmation" in out
    assert "looked at the image" in out
    assert fake.calls[0]["question"] == "what kind of dialog is this?"
    assert fake.calls[0]["model"] == "fake/vision"


def test_the_image_is_downscaled_and_reencoded_before_it_is_sent(image):
    """A 2400x1600 PNG must not be shipped as-is: one look should cost tokens, not MB."""
    sent = va._encode(image)
    raw = base64.b64decode(sent)
    assert len(raw) < image.stat().st_size, "encoded payload is not smaller than the file"
    assert raw[:2] == b"\xff\xd8", "expected a JPEG, got something else"


def test_no_question_is_refused_before_any_capture(monkeypatch):
    monkeypatch.setattr(va, "_capture", lambda *_: pytest.fail("captured without a question"))
    assert "needs a question" in va.analyze_image("   ")


def test_a_missing_file_is_reported_plainly(tmp_path):
    assert "no image at" in va.analyze_image("what is this?", path=str(tmp_path / "nope.png"))


def test_when_vision_is_down_it_says_so_and_returns_text_instead(image, monkeypatch):
    """Degrading to OCR is fine; pretending the picture was seen is not."""
    monkeypatch.setattr(va, "_cloud", lambda: FakeCloud(fail=True))
    from tools import ocr
    monkeypatch.setattr(ocr, "execute", lambda *a, **k: "ERROR 402: card declined")
    out = va.analyze_image("what does the error say?", path=str(image))
    assert "vision unavailable" in out
    assert "card declined" in out
    assert "I could not look at" in out


def test_an_empty_model_reply_is_treated_as_a_failure(image, monkeypatch):
    monkeypatch.setattr(va, "_cloud", lambda: FakeCloud(reply="   "))
    from tools import ocr
    monkeypatch.setattr(ocr, "execute", lambda *a, **k: "fallback text")
    out = va.analyze_image("what is this?", path=str(image))
    assert "vision unavailable" in out and "fallback text" in out


def test_region_full_screen_uses_a_full_capture(monkeypatch):
    calls = []
    monkeypatch.setattr(va.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(va, "_encode", lambda p: "abc")
    monkeypatch.setattr(va, "_cloud", lambda: FakeCloud())
    va.analyze_image("what is on screen?", region="full_screen")
    assert any("-x" in c and not any("-R" in x for x in c) for c in calls), calls
