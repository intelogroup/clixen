"""
Local vision — no ML models needed. Uses Tesseract OCR + OpenCV contour detection
to understand screenshots. Replaces qwen3-vl for most screenshot analysis tasks.

Pipeline:
    1. Load screenshot (Pillow)
    2. Preprocess (grayscale, threshold, denoise)
    3. OCR text with bounding boxes (pytesseract)
    4. Detect UI elements via contour analysis (OpenCV)
    5. Merge text regions with element contours
    6. Return structured description with positions
    
Each element is identified by type (button, input, link, text) with coordinates
that can be used for clicking via AppleScript.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("local_vision")

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    import pytesseract
except ImportError as e:
    cv2 = np = Image = ImageDraw = ImageFont = pytesseract = None
    _IMPORT_ERROR = str(e)


_SCREENSHOT_DIR = Path.home() / "Downloads"

# Element color signatures (BGR for OpenCV)
_BUTTON_COLORS = {
    "blue": ([100, 50, 0], [140, 120, 80]),      # Primary buttons
    "green": ([40, 100, 40], [80, 180, 80]),       # Success/continue
    "red": ([40, 40, 100], [80, 80, 180]),         # Danger/delete
    "gray": ([100, 100, 100], [180, 180, 180]),    # Neutral buttons
    "white": ([200, 200, 200], [255, 255, 255]),   # White buttons
}

_ELEMENT_TYPES = {
    "button": r"\b(sign|login|submit|continue|save|delete|add|create|search|go|next|back|cancel|confirm|close|ok|done|send|buy|order|checkout|pay|register|join|start|get|try|install|download|upload)\b",
    "input": r"\b(email|password|username|search|phone|name|address|city|zip|code|card|number|date|text|enter|type)\b",
    "link": r"\b(link|more|details|here|learn|about|help|terms|privacy|contact|home|account|orders|history|profile|settings|logout)\b",
}


def _check_deps():
    if cv2 is None:
        return f"[local-vision] Missing deps: {_IMPORT_ERROR}. Run: pip install opencv-python pillow pytesseract && brew install tesseract"
    return None


def ocr(image_path: str, lang: str = "eng+fra") -> list[dict]:
    """
    Run OCR on an image and return text with bounding boxes.
    
    Returns: [{text, x, y, w, h, confidence}]
    """
    deps = _check_deps()
    if deps:
        return [{"error": deps}]

    img = cv2.imread(image_path)
    if img is None:
        return [{"error": f"Cannot read image: {image_path}"}]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    data = pytesseract.image_to_data(thresh, lang=lang, output_type=pytesseract.Output.DICT)
    results = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        if not text or int(data["conf"][i]) < 30:
            continue
        results.append({
            "text": text,
            "x": data["left"][i],
            "y": data["top"][i],
            "w": data["width"][i],
            "h": data["height"][i],
            "confidence": int(data["conf"][i]),
        })
    return results


def detect_elements(image_path: str) -> list[dict]:
    """
    Detect UI elements (buttons, input fields, cards) via contour analysis.
    Returns: [{type, text, x, y, w, h, confidence}]
    """
    deps = _check_deps()
    if deps:
        return [{"error": deps}]

    img = cv2.imread(image_path)
    if img is None:
        return [{"error": f"Cannot read: {image_path}"}]

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Edge detection
    edges = cv2.Canny(gray, 50, 150)
    
    # Dilate to close gaps
    kernel = np.ones((3, 3), np.uint8)
    dilated = cv2.dilate(edges, kernel, iterations=1)

    # Find contours
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Get OCR text with positions
    ocr_results = ocr(image_path)

    elements = []
    min_area = w * h * 0.001  # 0.1% of page area
    max_area = w * h * 0.8   # 80% of page area

    for contour in contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        area = cw * ch
        if area < min_area or area > max_area:
            continue
        if cw < 30 or ch < 15:  # Too small to be a UI element
            continue

        # Classify element type
        aspect = cw / max(ch, 1)
        element_type = "card" if aspect > 3 else "block"

        # Check if it looks like a button (compact, solid fill)
        roi = gray[y:y+ch, x:x+cw]
        mean_intensity = np.mean(roi)
        if 30 < cw < 400 and 20 < ch < 80 and aspect > 1.5:
            if mean_intensity < 50:  # Dark fill = button
                element_type = "button"
            elif mean_intensity > 200:  # Light fill = input
                element_type = "input"

        # Find closest OCR text
        matched_text = ""
        for ocr_item in ocr_results:
            ox, oy = ocr_item["x"], ocr_item["y"]
            if x <= ox <= x + cw and y <= oy <= y + ch:
                matched_text += " " + ocr_item["text"]
            elif abs(ox - x) < 20 and abs(oy - y) < 20:
                matched_text += " " + ocr_item["text"]

        matched_text = matched_text.strip()[:80]

        # Refine type based on matched text
        if matched_text:
            for etype, pattern in _ELEMENT_TYPES.items():
                if re.search(pattern, matched_text, re.I):
                    element_type = etype
                    break

        elements.append({
            "type": element_type,
            "text": matched_text,
            "x": x, "y": y, "w": cw, "h": ch,
            "area": int(area),
        })

    # Merge overlapping elements (keep the larger one)
    merged = []
    elements.sort(key=lambda e: -e["area"])
    for el in elements:
        overlap = False
        for existing in merged:
            ix = max(el["x"], existing["x"])
            iy = max(el["y"], existing["y"])
            ix2 = min(el["x"] + el["w"], existing["x"] + existing["w"])
            iy2 = min(el["y"] + el["h"], existing["y"] + existing["h"])
            if ix < ix2 and iy < iy2:
                iarea = (ix2 - ix) * (iy2 - iy)
                if iarea / el["area"] > 0.5:
                    overlap = True
                    break
        if not overlap:
            merged.append(el)

    return merged


def local_vision_snap(output_path: str = "") -> str:
    """
    Run OCR + contour detection on an existing screenshot file, return structured
    page description. Caller must supply output_path — no browser-capture backend
    is wired in (BrowserOS removed).
    """
    if not output_path:
        output_path = str(_SCREENSHOT_DIR / "local_vision_snap.png")

    if not Path(output_path).exists():
        return f"[local-vision] Screenshot file not found: {output_path}. Capture one first (no browser-capture backend wired in)."

    # OCR
    ocr_results = ocr(output_path)
    ocr_errors = [r for r in ocr_results if "error" in r]
    if ocr_errors:
        return f"[local-vision] OCR error: {ocr_errors[0]['error']}"

    # Element detection
    elements = detect_elements(output_path)

    # Build response
    lines = [
        f"--- Local Vision Analysis ({len(ocr_results)} text blocks, {len(elements)} UI elements) ---",
        "",
        "Detected text:",
    ]
    for r in ocr_results[:20]:
        lines.append(f'  "{r["text"]}" at ({r["x"]},{r["y"]}) conf={r["confidence"]}%')

    if len(ocr_results) > 20:
        lines.append(f"  ... and {len(ocr_results) - 20} more text blocks")

    lines.extend(["", "UI elements:"])
    for el in elements[:15]:
        lines.append(f'  [{el["type"]:6s}] "{el["text"][:50]}" at ({el["x"]},{el["y"]}) size={el["w"]}x{el["h"]}')

    if len(elements) > 15:
        lines.append(f"  ... and {len(elements) - 15} more elements")

    return "\n".join(lines)


def local_vision_highlight(image_path: str = "", output_path: str = "") -> str:
    """
    Annotate a screenshot with detected element bounding boxes and labels.
    Returns path to annotated image.
    """
    deps = _check_deps()
    if deps:
        return deps

    if not image_path:
        image_path = str(_SCREENSHOT_DIR / "local_vision_snap.png")

    if not Path(image_path).exists():
        return f"[local-vision] File not found: {image_path}"

    if not output_path:
        output_path = image_path.replace(".png", "_annotated.png")

    elements = detect_elements(image_path)
    img = cv2.imread(image_path)
    colors = {"button": (0, 255, 0), "input": (255, 0, 0), "link": (0, 0, 255), "text": (255, 255, 0), "card": (200, 200, 0), "block": (128, 128, 128)}

    for el in elements:
        color = colors.get(el["type"], (255, 255, 255))
        cv2.rectangle(img, (el["x"], el["y"]), (el["x"] + el["w"], el["y"] + el["h"]), color, 2)
        label = f'{el["type"]}: {el["text"][:30]}'
        cv2.putText(img, label, (el["x"], el["y"] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

    cv2.imwrite(output_path, img)
    return f"Annotated screenshot saved: {output_path}"


# =========================================================================
# Schemas
# =========================================================================

LOCAL_VISION_SNAP_SCHEMA = {
    "type": "function", "function": {
        "name": "local_vision_snap",
        "description": (
            "Analyze a screenshot using local OCR (Tesseract) + contour detection (OpenCV). "
            "No ML models needed. Returns detected text blocks with positions and UI element types. "
            "Faster than qwen3-vl (~1s vs 10s+). "
            "Use to understand page layout, find text on screen, or verify visual appearance."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

LOCAL_VISION_HIGHLIGHT_SCHEMA = {
    "type": "function", "function": {
        "name": "local_vision_highlight",
        "description": (
            "Annotate a screenshot with detected element bounding boxes and labels. "
            "Saves an _annotated.png version showing what the system detected."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

SCHEMAS = [LOCAL_VISION_SNAP_SCHEMA, LOCAL_VISION_HIGHLIGHT_SCHEMA]
