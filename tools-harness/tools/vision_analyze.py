"""Look at an image the way a person does — not OCR, understanding.

`ocr_image` returns characters. That answers "what does the error message say" and
nothing else: it cannot tell you that a dialog is a payment confirmation, that a
chart is trending down, that the button you need is greyed out, or that the page is
a mind map about a medical topic. Those are the questions a desktop agent actually
gets, and they need a vision model.

Measured on this machine against a live screen (MedicoSpira exam page):

    ocr_image      → "Question Id: 15270 / Lab Values Notes Calculator ..."
    analyze_image  → "MedicoSpira is showing a USMLE Step 1 exam mind map on
                      nephrolithiasis, with the item notes editor open."   (1.4 s)

So this is a separate tool rather than a smarter `ocr_image`: different question,
different cost, different failure mode.

Costs and limits, stated plainly:
- one model call per look, on the vision tier (``CLOUD_VISION_MODEL``); the image
  is downscaled to ~1400 px and re-encoded as JPEG so a full-screen capture ships
  in a couple hundred KB rather than 800 KB of PNG;
- the screen leaves the machine. That is the trade for understanding; `ocr_image`
  stays the local option;
- if the vision tier is unreachable the tool degrades to OCR text and says so,
  rather than pretending it saw the picture.
"""

from __future__ import annotations

import base64
import io
import subprocess
from pathlib import Path

_MAX_EDGE = 1400
_JPEG_QUALITY = 82

ANALYZE_IMAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "analyze_image",
        "description": (
            "Look at the screen (or an image file) and answer a question about it. Use this "
            "when the answer needs understanding rather than reading: what kind of dialog is "
            "this, is that button enabled, what does this chart show, what state is this app "
            "in, is anything in the way. It sees layout, colour, icons and images; ocr_image "
            "only returns characters."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What to find out about the image, in one sentence.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional image file. Omit to analyse a fresh screenshot.",
                },
                "region": {
                    "type": "string",
                    "enum": ["front_window", "full_screen"],
                    "description": "What to capture when path is omitted. Default front_window.",
                },
            },
            "required": ["question"],
        },
    },
}


def _front_window_bounds() -> tuple[int, int, int, int] | None:
    """Bounds of the frontmost ordinary window, from WindowServer.

    CGWindowListCopyWindowInfo is ordered front-to-back, so the first on-screen
    layer-0 window that is not tiny is the one the user is looking at. Layer 0
    excludes the menu bar, the Dock and our own overlays (which sit at 5 and 25).
    """
    try:
        import Quartz

        for w in Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
        ):
            if w.get("kCGWindowLayer") != 0:
                continue
            b = w.get("kCGWindowBounds") or {}
            width, height = int(b.get("Width", 0)), int(b.get("Height", 0))
            if width < 200 or height < 150:
                continue
            return int(b["X"]), int(b["Y"]), width, height
    except Exception:
        return None
    return None


def _capture(region: str) -> tuple[Path, str]:
    """Screenshot the requested area. Returns (path, human description)."""
    region = (region or "front_window").strip().lower()
    bounds = None if region == "full_screen" else _front_window_bounds()
    if bounds:
        x, y, w, h = bounds
        path = Path("/tmp/clixen_vision_window.png")
        subprocess.run(
            ["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(path)],
            check=True, timeout=30,
        )
        return path, f"the front window ({w}x{h})"

    path = Path("/tmp/clixen_vision_full.png")
    subprocess.run(["screencapture", "-x", str(path)], check=True, timeout=30)
    note = "the whole screen" if region == "full_screen" else "the whole screen (no front window found)"
    return path, note


def _encode(path: Path) -> str:
    """Downscale and re-encode, so one look costs tokens rather than megabytes."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("RGB")
            longest = max(im.size)
            if longest > _MAX_EDGE:
                scale = _MAX_EDGE / longest
                im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=_JPEG_QUALITY)
            return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        # No PIL, or an unreadable image: send the file as it is rather than failing.
        return base64.b64encode(path.read_bytes()).decode()


def _cloud():
    """The cloud client, behind a seam.

    Imported lazily so the OCR fallback still works when the client cannot load, and
    patched directly by tests — monkeypatching sys.modules turned out to be
    order-dependent once another suite had imported the real client.
    """
    from clients import cloud_client

    return cloud_client


def analyze_image(question: str, path: str = "", region: str = "front_window") -> str:
    question = (question or "").strip()
    if not question:
        return "[error] analyze_image needs a question — what should I look for?"

    if path:
        image_path = Path(path).expanduser()
        if not image_path.is_file():
            return f"[error] no image at {path}"
        where = f"the image {image_path.name}"
    else:
        try:
            image_path, where = _capture(region)
        except Exception as exc:
            return f"[error] could not capture the screen: {exc}"

    try:
        cloud = _cloud()
        reply = cloud.chat(
            question,
            model=cloud.CLOUD_VISION_MODEL,
            images=[_encode(image_path)],
            max_rounds=1,
        )
        answer = str(reply).strip()
        if not answer:
            raise RuntimeError("the vision model returned nothing")
        return f"{answer}\n\n(looked at {where})"
    except Exception as exc:
        # Degrade honestly: text is still useful, but say the picture was not seen.
        from tools import ocr

        text = ocr.execute(str(image_path))
        return (
            f"[vision unavailable: {exc}]\n"
            f"I could not look at {where}, so this is only the text found in it:\n{text}"
        )
