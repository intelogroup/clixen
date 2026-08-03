"""ocr_image (PaddleOCR, CPU-only) measured 28.4s on a single full-resolution
screenshot (trace_store run 51c47055, 2026-07-10) -- the entire "what's on my
screen" latency was this one call. _inject_screenshot_image() attaches the
screenshot directly to the next turn for vision-capable models instead of
leaving the model to call ocr_image, bypassing the CPU bottleneck.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import clients.cloud_client as cc


def test_injects_image_on_vision_model_screenshot_success():
    png_path = Path(__file__).resolve().parent / "_tmp_screenshot.png"
    png_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    try:
        messages = []
        result = f"Screenshot saved: {png_path} (12 KB, target=screen)."
        cc._inject_screenshot_image(messages, "take_screenshot", result, cc.CLOUD_VISION_MODEL)
        assert len(messages) == 1, messages
        assert messages[0]["role"] == "user"
        blocks = messages[0]["content"]
        assert any(b["type"] == "image_url" for b in blocks), blocks
    finally:
        png_path.unlink(missing_ok=True)


def test_no_injection_on_non_vision_model():
    messages = []
    cc._inject_screenshot_image(
        messages, "take_screenshot", "Screenshot saved: /tmp/x.png (1 KB).",
        cc.DEFAULT_CLOUD_MODEL,
    )
    assert messages == []


def test_no_injection_for_other_tools():
    messages = []
    cc._inject_screenshot_image(
        messages, "ocr_image", "some text", cc.CLOUD_VISION_MODEL,
    )
    assert messages == []


def test_no_injection_on_screenshot_failure():
    messages = []
    cc._inject_screenshot_image(
        messages, "take_screenshot", "[error] peekaboo invocation failed: x",
        cc.CLOUD_VISION_MODEL,
    )
    assert messages == []


if __name__ == "__main__":
    test_injects_image_on_vision_model_screenshot_success()
    test_no_injection_on_non_vision_model()
    test_no_injection_for_other_tools()
    test_no_injection_on_screenshot_failure()
    print("ok")
