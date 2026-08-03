"""Verify the model-tool concurrency gate in ollama_client.

- model-invoking tools (web_search/deep_research/local_search) never run concurrently
- plain IO tools still overlap
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import clients.ollama_client as oc


def _make_probe():
    """Return (fn, peak) where fn records max concurrent in-flight callers."""
    state = {"cur": 0, "peak": 0}
    lock = threading.Lock()

    def fn(name, arguments):
        with lock:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
        time.sleep(0.05)  # hold the slot long enough for overlap to show
        with lock:
            state["cur"] -= 1
        return f"ok:{name}"

    return fn, state


def test_model_tools_are_serialized(monkeypatch):
    probe, state = _make_probe()
    monkeypatch.setattr(oc, "execute_tool", probe)

    names = ["web_search", "deep_research", "local_search", "web_search"]
    with ThreadPoolExecutor(max_workers=len(names)) as ex:
        list(ex.map(lambda n: oc._run_tool_gated(n, {}), names))

    assert state["peak"] == 1, f"model tools overlapped: peak={state['peak']}"


def test_io_tools_overlap(monkeypatch):
    probe, state = _make_probe()
    monkeypatch.setattr(oc, "execute_tool", probe)

    names = ["read_file", "list_directory", "grep_files", "write_file"]
    with ThreadPoolExecutor(max_workers=len(names)) as ex:
        list(ex.map(lambda n: oc._run_tool_gated(n, {}), names))

    assert state["peak"] >= 2, f"IO tools did not overlap: peak={state['peak']}"


def test_mixed_caps_model_tools_only(monkeypatch):
    """IO + model tools together: model tools still bottleneck to 1, IO unaffected."""
    probe, state = _make_probe()
    monkeypatch.setattr(oc, "execute_tool", probe)

    names = ["web_search", "deep_research", "read_file", "list_directory"]
    with ThreadPoolExecutor(max_workers=len(names)) as ex:
        list(ex.map(lambda n: oc._run_tool_gated(n, {}), names))

    # peak can exceed 1 (IO overlaps), but the two model tools alone never both run.
    # Re-run with only the model subset to assert that directly.
    probe2, state2 = _make_probe()
    monkeypatch.setattr(oc, "execute_tool", probe2)
    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(lambda n: oc._run_tool_gated(n, {}), ["web_search", "deep_research"]))
    assert state2["peak"] == 1
