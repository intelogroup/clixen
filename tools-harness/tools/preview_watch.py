"""
Preview.app page watcher — tracks which page of which PDF the user has open in
macOS Preview (front-most window) via AppleScript, no browser/DOM involved.
Poller thread (wired into core.py) writes state to disk; the tool reads it and
extracts that page's text from the actual PDF file via PyMuPDF (not a screenshot).

Fence: front-most Preview window only, single document. Multi-window/other PDF
apps (Acrobat, Skim) not covered.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

_DATA_DIR = Path.home() / ".config" / "g4l" / "data"
_STATE_FILE = _DATA_DIR / "preview_watch_state.json"
_CACHE_FILE = _DATA_DIR / "preview_page_cache.json"
_CACHE_TTL_S = 600  # revisit a page within 10 min → skip re-extraction
_CACHE_MAX_ENTRIES = 50

_TITLE_RE = re.compile(r"^(?P<name>.*?)\s*[–-]\s*Page\s+(?P<page>\d+)\s+of\s+(?P<total>\d+)\s*$")

GET_PREVIEW_PAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_preview_current_page",
        "description": (
            "See what page of what PDF the user currently has open in macOS Preview.app "
            "(front-most window). Returns the extracted text of that exact page, plus "
            "filename and page/total count. Use when the user references 'this page', "
            "'what I'm reading', or asks a question about a PDF they have open in Preview "
            "without naming a file. Returns an error string if Preview isn't open or no "
            "page has been detected yet."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _osascript(script: str) -> str:
    try:
        out = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip()
    except Exception:
        return ""


def _poll_once() -> dict | None:
    running = _osascript(
        'tell application "System Events" to (name of processes) contains "Preview"'
    )
    if running != "true":
        return None
    title = _osascript(
        'tell application "System Events" to tell process "Preview" to get name of window 1'
    )
    m = _TITLE_RE.match(title)
    if not m:
        return None
    path = _osascript('tell application "Preview" to get path of document 1')
    if not path:
        return None
    return {
        "pdf_path": path,
        "filename": m.group("name"),
        "page": int(m.group("page")),
        "total_pages": int(m.group("total")),
        "updated_at": time.time(),
    }


def _load_cache() -> dict:
    try:
        return json.loads(_CACHE_FILE.read_text())
    except Exception:
        return {}


def _prune_cache(cache: dict) -> dict:
    now = time.time()
    cache = {k: v for k, v in cache.items() if now - v["ts"] < _CACHE_TTL_S}
    if len(cache) > _CACHE_MAX_ENTRIES:
        for k in sorted(cache, key=lambda k: cache[k]["ts"])[: len(cache) - _CACHE_MAX_ENTRIES]:
            del cache[k]
    return cache


def run_preview_watch_daemon(poll_seconds: float = 1.5):
    """Background loop — call from a daemon thread (see core.py). Page text is
    cached per (path, page) with a TTL, so re-visiting a recently-seen page
    (flip back, then forward again) skips re-running pdftotext entirely."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache = _load_cache()
    while True:
        try:
            state = _poll_once()
            if state is not None:
                key = f"{state['pdf_path']}#{state['page']}"
                now = time.time()
                cached = cache.get(key)
                if cached is not None and now - cached["ts"] < _CACHE_TTL_S:
                    state["text"] = cached["text"]
                else:
                    from tools.structured import read_pdf
                    text = read_pdf(state["pdf_path"], pages=str(state["page"]))
                    state["text"] = text
                    # Don't cache a failed extraction (e.g. iCloud file still a
                    # placeholder right after restart) — retry same page next tick.
                    if not text.startswith("PDF read error"):
                        cache[key] = {"text": text, "ts": now}
                        cache = _prune_cache(cache)
                        _CACHE_FILE.write_text(json.dumps(cache))
                _STATE_FILE.write_text(json.dumps(state))
        except Exception:
            pass
        time.sleep(poll_seconds)


def get_preview_current_page() -> str:
    if not _STATE_FILE.exists():
        return "No PDF page detected in Preview.app yet — is Preview open with a PDF?"
    try:
        state = json.loads(_STATE_FILE.read_text())
    except Exception:
        return "Preview watch state unreadable."

    if time.time() - state.get("updated_at", 0) > 30:
        return "Preview.app doesn't appear to be open with a PDF right now (stale state)."

    return (
        f"Preview.app: {state['filename']} — page {state['page']} of {state['total_pages']}"
        f"\n\n{state.get('text', '')}"
    )
