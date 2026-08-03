"""opencode tool — delegate coding tasks to opencode's own agent via `opencode serve`.

Flow: ensure `opencode serve` is up, create a session, send the prompt, poll
/api/session/{id}/message for the assistant reply, return it plus a web URL
for the human to inspect the session live.

NOTE: opencode's serve build-agent model is config-controlled (opencode.json);
the per-request `model` field is ignored by serve, so a working default model
must be configured for real replies to appear.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

SERVE_URL = os.environ.get("OPENCODE_SERVE_URL", "http://127.0.0.1:4990").rstrip("/")
POLL_TIMEOUT = int(os.environ.get("ASK_OPENCODE_TIMEOUT", "300"))
# model forced for serve sessions (serve ignores string/query model, wants a Model.Ref object)
DEFAULT_MODEL = os.environ.get("ASK_OPENCODE_MODEL", "nvidia/nemotron-3-super-120b-a12b")
# free fallback (OpenCode Zen Nemotron 3 Ultra Free) if the paid nvidia model is unavailable
# ("opencode/nemotron-3-super-free" doesn't exist in the catalog — confirmed via
# `opencode models`, live 401 "Model nemotron-3-super-free is not supported")
FALLBACK_MODEL = os.environ.get("ASK_OPENCODE_FALLBACK_MODEL", "opencode/nemotron-3-ultra-free")
OPENCODE_CONFIG = os.path.expanduser("~/.config/opencode/opencode.json")


def _model_ref(spec: str) -> dict:
    spec = spec.strip()
    if "/" in spec:
        provider, mid = spec.split("/", 1)
        return {"id": mid, "providerID": provider}
    return {"id": spec, "providerID": "opencode"}

ASK_OPENCODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_opencode",
        "description": (
            "Delegate a coding task to the opencode agent (a general-purpose AI coding CLI). "
            "Use for any task that involves reading, editing, writing, or refactoring source "
            "code across multiple files, running tests, or git operations. "
            "The opencode agent has full filesystem access to the project directory, can "
            "search code, edit files, run commands, and use git. "
            "It is especially suitable for multi-file refactors, bug fixes requiring investigation, "
            "test writing, and any task where a second AI agent working independently would help."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The coding task to perform. Be specific: include file paths, "
                        "expected behavior, error messages, and constraints. "
                        "Example: 'In tools/reminder.py, add a dry_run parameter that logs "
                        "instead of scheduling. Update SET_REMINDER_SCHEMA description too.'"
                    ),
                },
            },
            "required": ["query"],
        },
    },
}


def _http(method, path, *, json_body=None, params=None, timeout=60):
    url = SERVE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(json_body).encode() if json_body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _serve_healthy() -> bool:
    try:
        _http("GET", "/api/health", timeout=5)
        return True
    except Exception:
        return False


def _ensure_serve() -> str:
    if _serve_healthy():
        return SERVE_URL
    port = SERVE_URL.rsplit(":", 1)[-1]
    subprocess.Popen(
        ["opencode", "serve", "--port", port],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(40):
        time.sleep(0.5)
        if _serve_healthy():
            return SERVE_URL
    raise RuntimeError("opencode serve did not start")


def _extract_text(msg: dict) -> str:
    bits = []
    for part in msg.get("content") or []:
        if isinstance(part, dict):
            if part.get("type") == "text":
                bits.append(part.get("text", ""))
        elif isinstance(part, str):
            bits.append(part)
    # fallbacks for other shapes
    if not bits and msg.get("text"):
        bits.append(msg["text"])
    return "\n".join(b for b in bits if b).strip()


def _set_config_model(model: str) -> None:
    """Rewrite opencode.json's top-level model (serve reads this at startup)."""
    import tempfile
    try:
        with open(OPENCODE_CONFIG) as f:
            cfg = json.load(f)
        if cfg.get("model") != model:
            cfg["model"] = model
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OPENCODE_CONFIG))
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(cfg, f, indent=2)
                os.rename(tmp, OPENCODE_CONFIG)
            except Exception:
                os.unlink(tmp)
                raise
    except Exception:
        pass


def _restart_serve_for(model: str) -> str:
    """Point config at `model` and respawn serve so it picks it up."""
    _set_config_model(model)
    subprocess.run(["pkill", "-f", "opencode serve"], stderr=subprocess.DEVNULL)
    time.sleep(1)
    return _ensure_serve()


def _run_opencode(query: str, model: str) -> str:
    cwd = os.getcwd()
    try:
        _ensure_serve()
    except Exception as e:
        return f"[opencode error] serve unavailable: {e}"

    try:
        session = _http(
            "POST",
            "/api/session",
            json_body={"directory": cwd, "workspace": cwd, "model": _model_ref(model)},
        )
        sid = session["data"]["id"]
    except Exception as e:
        return f"[opencode error] could not create session: {e}"

    try:
        _http(
            "POST",
            f"/api/session/{sid}/prompt",
            json_body={"prompt": {"text": query}, "stream": True, "auto": True},
            timeout=60,
        )
    except Exception as e:
        return f"[opencode error] could not send prompt: {e}"

    reply = ""
    error = None
    deadline = time.time() + POLL_TIMEOUT
    consec_fails = 0
    while time.time() < deadline:
        time.sleep(3)
        try:
            msgs = _http("GET", f"/api/session/{sid}/message", params={"limit": 20})["data"]
            consec_fails = 0
        except Exception:
            consec_fails += 1
            if consec_fails >= 5:
                return f"[opencode error] server unreachable after {consec_fails} consecutive failures"
            continue
        for m in msgs:
            if m.get("type") != "assistant":
                continue
            fin = m.get("finish")
            if fin == "stop":
                reply = _extract_text(m)
            elif fin == "error":
                error = m.get("error", {}).get("message", "unknown error")
        if reply or error:
            break

    url = f"{SERVE_URL}/"
    if error:
        return f"[opencode session error] {error}\n(session: {url} — id {sid})"
    if not reply:
        return f"[opencode] no reply captured (session: {url} — id {sid})"
    return f"{reply}\n\n[opencode session: {url} — id {sid}]"


def exec_ask_opencode(args: dict) -> str:
    query = args["query"]
    result = _run_opencode(query, DEFAULT_MODEL)
    if result.startswith("[opencode"):
        # primary (paid nvidia nemotron) failed — fall back to free Zen Nemotron
        try:
            _restart_serve_for(FALLBACK_MODEL)
            result = _run_opencode(query, FALLBACK_MODEL)
        except Exception as e:
            result = f"{result}\n[opencode fallback also failed] {e}"
    return result
