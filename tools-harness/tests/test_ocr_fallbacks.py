def test_ocr_prefers_surya_over_tesseract(tmp_path, monkeypatch):
    from tools import ocr

    image = tmp_path / "slide.webp"
    image.write_bytes(b"image")
    monkeypatch.setattr("tools.surya_ocr._ocr_daemon", lambda *args, **kwargs: "surya text")
    monkeypatch.setattr(ocr, "_tesseract", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not used")))

    assert ocr.execute(str(image)) == "surya text"


def test_ocr_falls_back_to_tesseract_when_surya_unavailable(tmp_path, monkeypatch):
    from tools import ocr

    image = tmp_path / "scan.webp"
    image.write_bytes(b"image")
    monkeypatch.setattr("tools.surya_ocr._ocr_daemon", lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("offline")))
    monkeypatch.setattr(ocr, "_tesseract", lambda *args, **kwargs: "tesseract text")

    assert ocr.execute(str(image)) == "tesseract text"
