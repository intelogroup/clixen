"""Peekaboo screenshot wrapper.

Wraps the `peekaboo` CLI (https://github.com/steipete/Peekaboo) for screen capture.
Requires macOS Screen Recording permission granted to the parent process (Terminal,
iTerm, or the Tauri app shell at runtime).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

_DEFAULT_DIR = Path.home() / ".clixen" / "screenshots"


LIST_WINDOWS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_windows",
        "description": (
            "List currently open windows on the user's Mac. "
            "If 'app' is provided, returns windows for that app only; otherwise "
            "returns the list of running apps (their names + PIDs). "
            "Use to discover what's open before taking a screenshot of a specific window, "
            "or to answer 'what apps are running'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "Optional app name (e.g. 'Safari'). If omitted, lists running apps.",
                },
            },
            "required": [],
        },
    },
}


SCHEMA = {
    "type": "function",
    "function": {
        "name": "take_screenshot",
        "description": (
            "Capture a screenshot of the user's Mac screen or a specific app window. "
            "Use when the user asks to see, look at, capture, or describe what's on screen, "
            "or when visual context is needed to answer (UI bug, layout question, "
            "what-is-this-error, what's-on-my-screen). "
            "Returns the absolute file path of the saved PNG and basic metadata. "
            "The path can then be passed to ocr_image for text extraction or to a "
            "vision-capable model for description."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": (
                        "What to capture. 'screen' = entire main display (default). "
                        "'window' = a specific app's frontmost window (requires app)."
                    ),
                    "enum": ["screen", "window"],
                    "default": "screen",
                },
                "app": {
                    "type": "string",
                    "description": (
                        "App name when mode='window' (e.g. 'Safari', 'Notes', 'Xcode'). "
                        "Ignored for mode='screen'."
                    ),
                },
            },
            "required": [],
        },
    },
}


def _ensure_binary() -> str | None:
    path = shutil.which("peekaboo")
    if path:
        return path
    return None


def execute(mode: str = "screen", app: str = "") -> str:
    binary = _ensure_binary()
    _DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _DEFAULT_DIR / f"shot-{int(time.time())}.png"

    if not binary:
        if sys.platform != "darwin":
            return "[error] no screenshot binary found and native screencapture is macOS-only — install playwright/maim/scrot"
        # Fallback to macOS native screencapture
        cmd = ["/usr/sbin/screencapture", "-x", str(out_path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if result.returncode != 0:
                return f"[error] native screencapture failed: {result.stderr or result.stdout}"
        except Exception as e:
            return f"[error] native screencapture invocation failed: {e}"
            
        if not out_path.exists():
            return "[error] native screencapture did not produce a screenshot file."
            
        size_kb = out_path.stat().st_size // 1024
        target_desc = f"{mode}" + (f" of {app}" if app else "")
        return (
            f"Screenshot saved: {out_path} ({size_kb} KB, target={target_desc}) (fallback to native screencapture). "
            f"Pass this path to ocr_image to extract text, or reference it in a "
            f"vision-capable response."
        )

    cmd = [binary, "image", "--path", str(out_path), "--json-output"]
    if mode == "window":
        if not app:
            return "[error] mode='window' requires the 'app' parameter (e.g. 'Safari')."
        cmd += ["--mode", "window", "--app", app]
    else:
        cmd += ["--mode", "screen"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return "[error] peekaboo timed out after 20s — Screen Recording permission may be missing."
    except Exception as e:
        return f"[error] peekaboo invocation failed: {e}"

    if result.returncode != 0:
        combined = ((result.stderr or "") + (result.stdout or "")).lower()
        if "screen recording" in combined or "permission_error" in combined or "not authorized" in combined:
            return (
                "[permission denied] Grant Screen Recording to your terminal/app: "
                "System Settings → Privacy & Security → Screen Recording, then restart it."
            )
        if "accessibility" in combined and "permission" in combined:
            return (
                "[permission denied] Grant Accessibility to your terminal/app: "
                "System Settings → Privacy & Security → Accessibility, then restart it."
            )
        try:
            err_payload = json.loads(result.stdout) if result.stdout.strip() else {}
            err_msg = (err_payload.get("error") or {}).get("message")
            if err_msg:
                return f"[peekaboo error] {err_msg}"
        except json.JSONDecodeError:
            pass
        snippet = (result.stderr or result.stdout or "").strip()[:300]
        return f"[peekaboo error rc={result.returncode}] {snippet}"

    payload: dict = {}
    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        pass

    saved = out_path if out_path.exists() else None
    if not saved:
        files = payload.get("data", {}).get("saved_files") if isinstance(payload, dict) else None
        if isinstance(files, list) and files:
            first = files[0]
            if isinstance(first, dict) and first.get("path"):
                saved = Path(first["path"])

    if not saved or not saved.exists():
        return f"[peekaboo] no screenshot file produced. Raw output: {result.stdout[:300]}"

    size_kb = saved.stat().st_size // 1024
    target = f"{mode}" + (f" of {app}" if app else "")
    return (
        f"Screenshot saved: {saved} ({size_kb} KB, target={target}). "
        f"Pass this path to ocr_image to extract text, or reference it in a "
        f"vision-capable response."
    )


def list_windows(app: str = "") -> str:
    binary = _ensure_binary()
    if not binary:
        return "[peekaboo not installed] Install with: brew install steipete/tap/peekaboo"

    if app:
        cmd = [binary, "list", "windows", "--app", app, "--json"]
    else:
        cmd = [binary, "list", "apps", "--json"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "[error] peekaboo list timed out after 10s."
    except OSError as e:
        return f"[error] peekaboo list invocation failed: {e}"

    if result.returncode != 0:
        try:
            err = json.loads(result.stdout) if result.stdout.strip() else {}
            msg = (err.get("error") or {}).get("message")
            if msg:
                return f"[peekaboo error] {msg}"
        except json.JSONDecodeError:
            pass
        return f"[peekaboo error rc={result.returncode}] {(result.stderr or result.stdout).strip()[:300]}"

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return f"[peekaboo] non-JSON output: {result.stdout[:300]}"

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return f"[peekaboo] unexpected payload shape: {result.stdout[:300]}"

    if app:
        windows = data.get("windows") or []
        if not windows:
            return f"No open windows for {app}."
        lines = [f"Windows for {app} ({len(windows)}):"]
        for w in windows[:30]:
            title = (w.get("title") or "").strip() or "(untitled)"
            idx = w.get("index", "?")
            wid = w.get("window_id", "?")
            flags = []
            if w.get("isMinimized"):
                flags.append("minimized")
            if not w.get("isOnScreen", True):
                flags.append("offscreen")
            if w.get("isMainWindow"):
                flags.append("main")
            tail = f" [{', '.join(flags)}]" if flags else ""
            lines.append(f"  [{idx}] window_id={wid} {title}{tail}")
        return "\n".join(lines)

    apps = data.get("applications") or data.get("apps") or []
    if not apps:
        return "No running apps reported."
    visible = [a for a in apps if not a.get("isHidden")]
    lines = [f"Running apps ({len(visible)} visible / {len(apps)} total):"]
    for a in visible[:60]:
        name = a.get("name") or "(unknown)"
        pid = a.get("processIdentifier", "?")
        wc = a.get("windowCount", 0)
        active = " *" if a.get("isActive") else ""
        lines.append(f"  {name} (pid {pid}, windows {wc}){active}")
    return "\n".join(lines)
