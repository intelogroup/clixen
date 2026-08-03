"""
Python REPL tool — persistent Jupyter kernel for stateful code execution.

A single kernel process is shared across all calls in the harness session.
Variables, imports, and function definitions persist between run_python() calls.
Call reset_kernel() to wipe all state and start fresh.

Why Jupyter kernel (not subprocess per call):
  - Variables survive between turns — define a function in turn 1, call it in turn 3
  - Imports are cached — no re-importing pandas/numpy on every snippet
  - Rich output captured (stdout, stderr, repr, display_data)
  - Reset is instant (kernel restart, not process spawn)
"""

import queue
import threading
import time
from pathlib import Path

_HOME = str(Path.home())

# Module-level kernel singleton — lives for the duration of the harness process
_km = None   # KernelManager
_kc = None   # KernelClient
_lock = threading.Lock()


def _ensure_kernel():
    global _km, _kc
    try:
        import jupyter_client
    except ImportError:
        raise RuntimeError(
            "jupyter_client not installed. Run: pip install jupyter_client ipykernel"
        )

    if _km is not None and _km.is_alive():
        return _kc

    _km = jupyter_client.KernelManager(kernel_name="python3")
    _km.start_kernel()
    _kc = _km.client()
    _kc.start_channels()
    _kc.wait_for_ready(timeout=30)
    return _kc


def _collect_output(kc, msg_id: str, timeout: int) -> tuple[str, str | None]:
    """Drain iopub messages until kernel goes idle. Returns (output, error|None)."""
    parts = []
    error = None
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            msg = kc.get_iopub_msg(timeout=0.5)
        except queue.Empty:
            continue

        mtype = msg["header"]["msg_type"]
        content = msg["content"]

        if mtype == "stream":
            parts.append(content["text"])

        elif mtype in ("display_data", "execute_result"):
            data = content.get("data", {})
            text = data.get("text/plain", "")
            if text:
                prefix = "Out: " if mtype == "execute_result" else ""
                parts.append(f"{prefix}{text}\n")

        elif mtype == "error":
            # Strip ANSI codes from traceback for cleaner LLM output
            import re as _re
            tb = "\n".join(content.get("traceback", []))
            tb = _re.sub(r"\x1b\[[0-9;]*m", "", tb)
            error = f"{content['ename']}: {content['evalue']}\n{tb}"

        elif mtype == "status" and content.get("execution_state") == "idle":
            break

    return "".join(parts), error


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

RUN_PYTHON_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "Execute Python code in a persistent local kernel. "
            "Variables, imports, and function definitions survive between calls — "
            "you can build up state across multiple turns. "
            "Returns stdout, print output, and the repr of the last expression. "
            "Use for: running scripts, testing functions, data analysis, "
            "debugging, verifying code before writing it to a file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait for output (default 30)",
                    "default": 30,
                },
            },
            "required": ["code"],
        },
    },
}

RESET_KERNEL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reset_kernel",
        "description": (
            "Restart the Python kernel, clearing all variables, imports, and state. "
            "Use when you want a clean slate or the kernel is in a bad state."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

LIST_VARS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_kernel_vars",
        "description": "List all variables currently defined in the Python kernel.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

def run_python(code: str, timeout: int = 30) -> str:
    from tools.tool_policy import check_python_code_safety
    try:
        check_python_code_safety(code)
    except Exception as e:
        return f"[error] Security validation failed: {e}"

    timeout = min(int(timeout), 300)
    with _lock:
        try:
            kc = _ensure_kernel()
        except RuntimeError as e:
            return f"[error] {e}"

        msg_id = kc.execute(code)
        output, error = _collect_output(kc, msg_id, timeout)

    if error:
        result = f"[error]\n{error}"
        if output:
            result = f"{output.strip()}\n{result}"
        return result

    return output.strip() or "[no output]"


def reset_kernel() -> str:
    global _km, _kc
    with _lock:
        if _km is not None:
            try:
                _km.restart_kernel(now=True)
                _kc.wait_for_ready(timeout=15)
                return "Kernel restarted — all variables cleared."
            except Exception as e:
                # Force a full respawn
                try:
                    _km.shutdown_kernel(now=True)
                except Exception:
                    pass
                _km = None
                _kc = None
                return f"Kernel killed and will respawn on next run_python call. ({e})"
        return "No kernel running — will start fresh on next run_python call."


def list_kernel_vars() -> str:
    return run_python(
        "import sys\n"
        "_vars = {k: type(v).__name__ for k, v in globals().items() "
        "if not k.startswith('_') and k not in sys.stdlib_module_names}\n"
        "print('\\n'.join(f'{k}: {t}' for k, t in sorted(_vars.items())) or '(no user variables)')",
        timeout=10,
    )
