"""The OCR backend chain: which engine runs first, and what happens when one fails.

This chain is why `ocr_image` was dead in the shipped app: Surya's daemon is not
supervised and Tesseract lives outside the launchd PATH, so the old
Surya→Tesseract→PaddleOCR order returned "OCR unavailable" on every call. Vision
is part of the OS, so it cannot fail that way — but the ordering has to stay pinned
or a future edit reintroduces the dead default.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import ocr as ocr_mod  # noqa: E402


def _png(tmp_path):
    from PIL import Image
    p = tmp_path / "x.png"
    Image.new("RGB", (40, 20), (255, 255, 255)).save(p)
    return p


def test_vision_is_tried_before_tesseract(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(ocr_mod, "_vision", lambda p, lang="en": order.append("vision") or "from vision")
    monkeypatch.setattr(ocr_mod, "_tesseract", lambda p, lang="en": order.append("tesseract") or "from tess")
    out = ocr_mod.execute(str(_png(tmp_path)))
    assert out == "from vision"
    assert order == ["vision"], "tesseract must not run when Vision succeeds"


def test_a_failing_vision_falls_through_in_order(tmp_path, monkeypatch):
    order = []

    def boom(*_a, **_k):
        order.append("vision")
        raise RuntimeError("no Vision here")

    monkeypatch.setattr(ocr_mod, "_vision", boom)
    monkeypatch.setattr(ocr_mod, "_tesseract", lambda p, lang="en": order.append("tesseract") or "tess text")
    assert ocr_mod.execute(str(_png(tmp_path))) == "tess text"
    assert order == ["vision", "tesseract"]


def test_engine_override_pins_one_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIXEN_OCR_ENGINE", "tesseract")
    monkeypatch.setattr(ocr_mod, "_vision", lambda *a, **k: pytest.fail("Vision ran despite the override"))
    monkeypatch.setattr(ocr_mod, "_tesseract", lambda p, lang="en": "pinned")
    assert ocr_mod.execute(str(_png(tmp_path))) == "pinned"


def test_unknown_override_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIXEN_OCR_ENGINE", "magic")
    msg = ocr_mod.execute(str(_png(tmp_path)))
    assert "Unknown OCR engine" in msg


def test_every_engine_failing_reports_each_reason(tmp_path, monkeypatch):
    for name in ("_vision", "_tesseract", "_surya", "_paddle"):
        monkeypatch.setattr(ocr_mod, name, lambda *a, _n=name, **k: (_ for _ in ()).throw(RuntimeError(f"{_n} down")))
    msg = ocr_mod.execute(str(_png(tmp_path)))
    assert msg.startswith("OCR unavailable")
    for expected in ("Vision", "Tesseract", "Surya", "PaddleOCR"):
        assert expected in msg, f"{expected} failure reason missing from: {msg}"


@pytest.mark.skipif(sys.platform != "darwin", reason="Vision is macOS-only")
def test_vision_reads_real_text_on_this_machine(tmp_path):
    """End-to-end on the OS engine: draw a sentence, read it back."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (620, 70), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
    draw.text((12, 18), "Capitol completed in 1800", font=font, fill=(10, 10, 10))
    path = tmp_path / "sentence.png"
    img.save(path)

    text = ocr_mod._vision(str(path))
    assert "capitol" in text.lower() and "1800" in text
