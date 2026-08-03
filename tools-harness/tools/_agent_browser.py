"""
Shared agent-browser CLI helpers for connectors that drive the user's real,
logged-in Chrome (DoorDash/Uber accounts) via CDP `--auto-connect`. Replaces
the old BrowserOS-based `_browseros.py` (BrowserOS fully removed).

agent-browser has no prompt-injection content-boundary wrapper on by default
(unlike browseros-cli), so no strip step is needed on its output.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess

log = logging.getLogger("agent_browser_connector")

_BIN = shutil.which("agent-browser") or "agent-browser"
_DEFAULT_TIMEOUT_S = 30


def _run(args: list[str], timeout_s: int = _DEFAULT_TIMEOUT_S) -> str:
    """Run `agent-browser --auto-connect <args>`, return stdout or an
    `[agent-browser]`-prefixed error string. Never raises."""
    cmd = [_BIN, "--auto-connect", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError:
        return "[agent-browser] CLI not found — install via `npm i -g agent-browser`"
    except subprocess.TimeoutExpired:
        return f"[agent-browser] command timed out after {timeout_s}s: {' '.join(args)}"
    except Exception as e:
        return f"[agent-browser] {e}"
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        return f"[agent-browser] exit {r.returncode}: {err}"
    return r.stdout.strip()


def navigate(url: str, wait_load: bool = True) -> str:
    args = ["open", url]
    out = _run(args)
    if out.startswith("[agent-browser]"):
        return out
    if wait_load:
        _run(["wait", "--load", "networkidle"], timeout_s=15)
    return out


def eval_js(js: str) -> str:
    return _run(["eval", js])


def eval_json(js: str) -> dict:
    """eval_js + json.loads, with a structured error dict on any failure."""
    raw = eval_js(js)
    if raw.startswith("[agent-browser]"):
        return {"error": raw}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": f"could not parse eval result: {raw[:300]}"}


def get_text(selector: str = "body") -> str:
    return _run(["get", "text", selector])


def snapshot() -> str:
    """Accessibility tree with @refs, e.g. `@e3 [textbox] "Dropoff location"`."""
    return _run(["snapshot", "-i"])


def click(ref: str) -> str:
    return _run(["click", ref])


def fill(ref: str, text: str) -> str:
    return _run(["fill", ref, text])


def press(key: str) -> str:
    return _run(["press", key])


def wait(*args: str, timeout_s: int = 15) -> str:
    return _run(["wait", *args], timeout_s=timeout_s)
