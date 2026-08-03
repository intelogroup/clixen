# PaddleOCR Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ocr_image` tool backed by PaddleOCR that qwen2.5-coder:7b can call to extract text from image files.

**Architecture:** Create `tools/ocr.py` with the standard SCHEMA + execute() pattern, register it in `tools/registry.py`, add an `ocr` intent to `clients/router.py`, and handle it in `harness.py` to wire qwen7b + the tool. Apply all changes to both repo copies (~/Developer/ and ~/developer/).

**Tech Stack:** PaddleOCR (paddlepaddle + paddleocr), Python 3.12, miniforge3

---

## File Map

| Action | Path |
|--------|------|
| Create | `tools-harness/tools/ocr.py` |
| Modify | `tools-harness/tools/registry.py` |
| Modify | `tools-harness/clients/router.py` |
| Modify | `tools-harness/harness.py` |
| Sync (all 4 above) | `~/developer/clixen/tools-harness/` (lowercase repo) |

---

## Task 1: Install PaddleOCR

**Files:** none (system install)

- [ ] **Step 1: Install paddlepaddle (CPU, Apple Silicon compatible)**

```bash
pip install paddlepaddle
```

Expected: installs without error. PaddlePaddle ships a universal wheel for macOS ARM via miniforge.

- [ ] **Step 2: Install paddleocr**

```bash
pip install paddleocr
```

Expected: installs `paddleocr` and its deps (opencv, imgaug, pyclipper, etc).

- [ ] **Step 3: Smoke-test the install**

```python
# run as: python3 -c "..."
from paddleocr import PaddleOCR
ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
print("PaddleOCR import OK")
```

Expected: prints `PaddleOCR import OK`. First run downloads ~15 MB of detection/recognition models to `~/.paddleocr/`.

---

## Task 2: Create `tools/ocr.py`

**Files:**
- Create: `tools-harness/tools/ocr.py`

- [ ] **Step 1: Write the tool module**

Create `/Users/kalinovdameus/Developer/clixen/tools-harness/tools/ocr.py`:

```python
"""
OCR tool — PaddleOCR extracts text from image files.

Supports: PNG, JPG, JPEG, TIFF, BMP, WebP (anything OpenCV can decode).
Returns extracted text as plain lines, one per detected text region.
Model weights are downloaded on first call to ~/.paddleocr/ (~15 MB).
"""
from pathlib import Path

SCHEMA = {
    "type": "function",
    "function": {
        "name": "ocr_image",
        "description": (
            "Extract text from an image file using OCR. "
            "Use when the user provides an image path and wants to read text from it, "
            "or asks to OCR / scan / read text in a photo, screenshot, or document image. "
            "Returns the extracted text as plain lines."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "Absolute path to the image file (PNG, JPG, TIFF, BMP, WebP)"
                },
                "lang": {
                    "type": "string",
                    "description": "Language code: 'en' (default), 'fr', 'ch' (Chinese), 'ja', etc.",
                    "default": "en"
                }
            },
            "required": ["image_path"]
        }
    }
}


def execute(image_path: str, lang: str = "en") -> str:
    """Run PaddleOCR on the given image and return extracted text."""
    path = Path(image_path)
    if not path.exists():
        return f"File not found: {image_path}"
    if not path.is_file():
        return f"Not a file: {image_path}"

    try:
        from paddleocr import PaddleOCR
    except ImportError:
        return "PaddleOCR is not installed. Run: pip install paddlepaddle paddleocr"

    ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
    result = ocr.ocr(str(path), cls=True)

    if not result or not result[0]:
        return "No text detected in image."

    lines = []
    for line in result[0]:
        # line = [bbox, (text, confidence)]
        text, confidence = line[1]
        if confidence >= 0.5:
            lines.append(text)

    if not lines:
        return "No text detected with sufficient confidence."

    return "\n".join(lines)
```

- [ ] **Step 2: Quick manual test**

```bash
# Download a test image with text (or use any screenshot you have)
python3 -c "
from tools.ocr import execute
print(execute('/path/to/any/screenshot.png'))
"
```

Expected: prints extracted text lines. If no image is handy, skip — covered in Task 3's test.

---

## Task 3: Register in `tools/registry.py`

**Files:**
- Modify: `tools-harness/tools/registry.py`

- [ ] **Step 1: Add import at top of registry.py**

In `tools/registry.py`, after the last `from tools.X import ...` block (line 60, before `# All available tool schemas`), add:

```python
from tools.ocr import SCHEMA as OCR_SCHEMA, execute as ocr_execute
```

- [ ] **Step 2: Add schema to ALL_TOOLS**

In the `ALL_TOOLS` list (after `AUDIO_SCHEMA`, around line 65), add:

```python
    OCR_SCHEMA,
```

- [ ] **Step 3: Add executor to EXECUTORS**

In the `EXECUTORS` dict (after the `"transcribe_audio"` entry, around line 138), add:

```python
    "ocr_image": lambda args: ocr_execute(
        image_path=args["image_path"],
        lang=args.get("lang", "en"),
    ),
```

- [ ] **Step 4: Write the failing test**

Create a test in `tools-harness/test_ocr.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from tools.ocr import execute


def test_missing_file():
    result = execute("/nonexistent/image.png")
    assert "not found" in result.lower()


def test_ocr_returns_text():
    mock_result = [[[None, ("Hello World", 0.99)], [None, ("Second line", 0.95)]]]
    with patch("tools.ocr.PaddleOCR") as MockOCR:
        instance = MockOCR.return_value
        instance.ocr.return_value = mock_result
        # Create a temp file so the path.exists() check passes
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        try:
            result = execute(tmp)
        finally:
            os.unlink(tmp)
    assert "Hello World" in result
    assert "Second line" in result


def test_low_confidence_filtered():
    mock_result = [[[None, ("Junk", 0.2)], [None, ("Clear text", 0.9)]]]
    with patch("tools.ocr.PaddleOCR") as MockOCR:
        instance = MockOCR.return_value
        instance.ocr.return_value = mock_result
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        try:
            result = execute(tmp)
        finally:
            os.unlink(tmp)
    assert "Junk" not in result
    assert "Clear text" in result


def test_executor_in_registry():
    from tools.registry import EXECUTORS, execute_tool
    assert "ocr_image" in EXECUTORS
    result = execute_tool("ocr_image", {"image_path": "/nonexistent.png"})
    assert "not found" in result.lower()
```

- [ ] **Step 5: Run tests to verify they fail correctly**

```bash
cd /Users/kalinovdameus/Developer/clixen/tools-harness
python3 -m pytest test_ocr.py -v
```

Expected: `test_missing_file` and `test_executor_in_registry` may fail with ImportError until Step 1-3 are done. After completing Steps 1-3, re-run.

- [ ] **Step 6: Run tests — all should pass**

```bash
python3 -m pytest test_ocr.py -v
```

Expected:
```
test_ocr.py::test_missing_file PASSED
test_ocr.py::test_ocr_returns_text PASSED
test_ocr.py::test_low_confidence_filtered PASSED
test_ocr.py::test_executor_in_registry PASSED
4 passed
```

- [ ] **Step 7: Commit**

```bash
cd /Users/kalinovdameus/Developer/clixen
git add tools-harness/tools/ocr.py tools-harness/tools/registry.py tools-harness/test_ocr.py
git commit -m "feat: add PaddleOCR tool (ocr_image) with registry wiring"
```

---

## Task 4: Add `ocr` intent to `clients/router.py`

**Files:**
- Modify: `tools-harness/clients/router.py`

- [ ] **Step 1: Add OCR regex after `_TEMPORAL_RE` (around line 163)**

```python
_OCR_RE = re.compile(
    r"\b(ocr|extract text|read text (from|in)|scan (this |the )?(image|photo|screenshot|doc)|"
    r"what (does|do) (this|the) (image|photo|screenshot) say|"
    r"text in (this|the) (image|photo|screenshot|picture)|"
    r"get text from|transcribe (this )?(image|photo|screenshot))\b",
    re.IGNORECASE,
)
```

- [ ] **Step 2: Add OCR routing in `classify()` — before the library_docs check (line 184)**

In `classify()`, add as the first check (before `_LIBRARY_DOCS_RE`):

```python
    # OCR → qwen2.5-coder:7b + ocr_image tool (LangGraph JSON-in-content)
    if _OCR_RE.search(msg):
        return "qwen2.5-coder:7b", "ocr"
```

- [ ] **Step 3: Add OCR routing in `classify_telegram()` — before the code_quick check**

In `classify_telegram()`, add as the first check:

```python
    # OCR → qwen2.5-coder:7b + ocr_image tool
    if _OCR_RE.search(msg):
        return "qwen2.5-coder:7b", "ocr"
```

- [ ] **Step 4: Write routing test**

Add to `tools-harness/test_routing.py` (or create if it's not useful to add there — check first):

```python
from clients.router import classify, classify_telegram

def test_ocr_intent_web():
    model, intent = classify("extract text from this image /tmp/scan.png")
    assert intent == "ocr"
    assert model == "qwen2.5-coder:7b"

def test_ocr_intent_telegram():
    model, intent = classify_telegram("scan this screenshot")
    assert intent == "ocr"
    assert model == "qwen2.5-coder:7b"
```

- [ ] **Step 5: Run routing tests**

```bash
cd /Users/kalinovdameus/Developer/clixen/tools-harness
python3 -m pytest test_routing.py -v -k "ocr"
```

Expected: both new tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/kalinovdameus/Developer/clixen
git add tools-harness/clients/router.py tools-harness/test_routing.py
git commit -m "feat: add ocr intent to router → qwen2.5-coder:7b"
```

---

## Task 5: Wire OCR intent in `harness.py`

**Files:**
- Modify: `tools-harness/harness.py`

- [ ] **Step 1: Add ocr branch in the intent dispatch block**

In `harness.py`, find the `elif intent == "code_quick":` branch (around line 179). Add before it:

```python
    elif intent == "ocr":
        active_tools = _tool("ocr_image")
        # routed_model is already qwen2.5-coder:7b from router
```

- [ ] **Step 2: Verify qwen tool stripping does NOT apply here**

The `_manual_ide` stripping block (around line 191) only runs when `_manual_ide=True` (user manually picks a model in IDE chat). For normal chat/Telegram auto-routing to qwen via the `ocr` intent, `_manual_ide=False` so tools are preserved. No code change needed — just verify mentally.

- [ ] **Step 3: Quick integration smoke test**

```bash
cd /Users/kalinovdameus/Developer/clixen/tools-harness
python3 -c "
from clients.router import classify
model, intent = classify('read the text in this image /tmp/test.png')
print(f'model={model} intent={intent}')
from tools.registry import EXECUTORS
print('ocr_image in EXECUTORS:', 'ocr_image' in EXECUTORS)
"
```

Expected:
```
model=qwen2.5-coder:7b intent=ocr
ocr_image in EXECUTORS: True
```

- [ ] **Step 4: Commit**

```bash
cd /Users/kalinovdameus/Developer/clixen
git add tools-harness/harness.py
git commit -m "feat: wire ocr intent in harness → qwen7b + ocr_image tool"
```

---

## Task 6: Sync to lowercase repo

**Files:**
- Sync: `~/developer/clixen/tools-harness/tools/ocr.py`
- Sync: `~/developer/clixen/tools-harness/tools/registry.py`
- Sync: `~/developer/clixen/tools-harness/clients/router.py`
- Sync: `~/developer/clixen/tools-harness/harness.py`

- [ ] **Step 1: Copy all four modified files to the lowercase repo**

```bash
cp ~/Developer/clixen/tools-harness/tools/ocr.py \
   ~/developer/clixen/tools-harness/tools/ocr.py

cp ~/Developer/clixen/tools-harness/tools/registry.py \
   ~/developer/clixen/tools-harness/tools/registry.py

cp ~/Developer/clixen/tools-harness/clients/router.py \
   ~/developer/clixen/tools-harness/clients/router.py

cp ~/Developer/clixen/tools-harness/harness.py \
   ~/developer/clixen/tools-harness/harness.py
```

- [ ] **Step 2: Verify the copy**

```bash
diff ~/Developer/clixen/tools-harness/tools/ocr.py \
     ~/developer/clixen/tools-harness/tools/ocr.py
```

Expected: no output (files identical).

- [ ] **Step 3: Commit in lowercase repo**

```bash
cd ~/developer/clixen
git add tools-harness/tools/ocr.py tools-harness/tools/registry.py \
        tools-harness/clients/router.py tools-harness/harness.py
git commit -m "sync: paddleocr tool + ocr intent from capital-D repo"
```

---

## Task 7: Restart server and end-to-end test

- [ ] **Step 1: Restart the web UI server**

```bash
# Find and kill the running server (it auto-restarts via launchd or manual kill+relaunch)
pkill -f chat_ui.py
cd ~/Developer/clixen/tools-harness
python3 chat_ui.py &
```

Or if managed by launchd, reload the plist instead.

- [ ] **Step 2: Send an OCR message in the Web UI**

Open http://localhost:9234 in browser. In Auto mode, send:

```
ocr this image: /path/to/any/png/on/disk.png
```

Expected: qwen2.5-coder:7b calls `ocr_image`, PaddleOCR extracts text, model returns a reply quoting the extracted text.

- [ ] **Step 3: Verify in server logs**

```bash
tail -20 ~/Developer/clixen/tools-harness/chat_ui.log
```

Expected: log line showing `[router] model=qwen2.5-coder:7b intent=ocr` and a tool call to `ocr_image`.
