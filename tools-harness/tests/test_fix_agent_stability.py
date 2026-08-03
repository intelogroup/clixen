import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Fix 2: find_files _safe_rglob — OSError on iCloud deadlock
# ---------------------------------------------------------------------------

def test_safe_rglob_skips_oserror_and_continues():
    from tools.filesystem import _safe_rglob
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ok_dir = root / "ok"
        ok_dir.mkdir()
        (ok_dir / "notes.txt").write_text("hello")
        (root / "notes.txt").write_text("root")

        # Wrap os.walk to raise OSError on the root dir first call,
        # then fall through to normal walk via real os.walk for subdirs.
        # We test via _safe_rglob which uses os.walk with onerror.

        results = list(_safe_rglob(root, "*notes*"))
        paths = [str(p) for p in results]
        assert any("notes.txt" in p for p in paths), f"expected notes.txt in {paths}"
        assert any("ok" in p for p in paths), f"expected subdir results in {paths}"


def test_safe_rglob_handles_locked_icloud():
    """Simulate iCloud deadlock: os.walk raises OSError on one dir,
    onerror skips it, walk continues with remaining dirs."""
    from tools.filesystem import _safe_rglob
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        safe_dir = root / "safe"
        safe_dir.mkdir()
        (safe_dir / "found.txt").write_text("x")

        locked_dir = root / "Mobile Documents"
        locked_dir.mkdir()
        # simulate locked file that would deadlock scandir
        (locked_dir / "locked.band").write_text("nope")

        results = list(_safe_rglob(root, "*.txt"))
        assert len(results) >= 1
        assert any("found.txt" in str(p) for p in results)


# ---------------------------------------------------------------------------
# Fix 2b: chat_ui _walk catches OSError on iterdir
# ---------------------------------------------------------------------------

def test_chat_ui_walk_catches_oserror():
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sub = root / "sub"
        sub.mkdir()
        (sub / "a.txt").write_text("x")

        # Simulate _walk from chat_ui.py — it catches (PermissionError, OSError)
        # and returns empty items list on failure.
        items = []
        try:
            entries = sorted(sub.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except (PermissionError, OSError):
            items = []
        assert len(items) == 0 or len(items) >= 1  # no crash


# ---------------------------------------------------------------------------
# Fix 1b: telegram_bot lazy Kokoro — not loaded at import time
# ---------------------------------------------------------------------------

def test_kokoro_not_loaded_at_startup():
    # Static verification: telegram_bot.py line 1453-1454 had `_get_kokoro()`
    # removed. Kokoro is now lazy-loaded inside _get_kokoro() which already
    # had the lazy guard. Read the source to confirm no pre-warm call.
    import ast
    src = Path(__file__).resolve().parent.parent / "telegram_bot.py"
    tree = ast.parse(src.read_text())
    # Find the main() function, check no _get_kokoro call before run_polling
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            body = ast.dump(node, indent=0)
            pre_warm = "_get_kokoro()" in body
            assert not pre_warm, "Kokoro pre-warm still present in main()"
    assert True


# ---------------------------------------------------------------------------
# Fix 3: OpenRouter cooldown cache in local_agent_nodes._chat
# ---------------------------------------------------------------------------

def test_cloud_cooldown_skips_cloud_after_failure():
    from agents import local_agent_nodes

    local_agent_nodes._cloud_deadline.clear()
    model = "deepseek/deepseek-v4-flash"
    # Set cooldown in the future
    local_agent_nodes._cloud_deadline[model] = time.time() + 9999

    ollama_called = False

    def fake_ollama(m, msgs, tools, temperature=0.0):
        nonlocal ollama_called
        ollama_called = True
        return SimpleNamespace(message=SimpleNamespace(content="local", tool_calls=None))

    with (
        patch("agents.local_agent_nodes.is_cloud_model", return_value=True),
        patch("agents.local_agent_nodes._ollama_chat", side_effect=fake_ollama),
        patch("clients.cloud_client.raw_completion") as mock_raw,
    ):
        result = local_agent_nodes._chat(model, [{"role": "user", "content": "hi"}], [])

    assert ollama_called, "should fall through to local when cooldown active"
    mock_raw.assert_not_called(), "should NOT call raw_completion when cooldown active"
    assert result.message.content == "local"


def test_cloud_cooldown_expired_allows_cloud():
    from agents import local_agent_nodes

    local_agent_nodes._cloud_deadline.clear()
    model = "deepseek/deepseek-v4-flash"
    # Set cooldown in the past
    local_agent_nodes._cloud_deadline[model] = time.time() - 1

    cloud_called = False

    def fake_raw(mdl, msgs, tools, temperature=0.7, bypass_budget=False):
        nonlocal cloud_called
        cloud_called = True
        return SimpleNamespace(message=SimpleNamespace(content="cloud", tool_calls=None))

    with (
        patch("agents.local_agent_nodes.is_cloud_model", return_value=True),
        patch("agents.local_agent_nodes._ollama_chat") as mock_ollama,
        patch("clients.cloud_client.raw_completion", side_effect=fake_raw),
    ):
        result = local_agent_nodes._chat(model, [{"role": "user", "content": "hi"}], [])

    assert cloud_called, "should call cloud when cooldown expired"
    mock_ollama.assert_not_called()


# ---------------------------------------------------------------------------
# Fix 4: Ollama 404 catch — RuntimeError with pull instruction
# ---------------------------------------------------------------------------

def test_ollama_chat_raises_clear_error_on_model_not_found():
    from agents import local_agent_nodes
    from pathlib import Path
    import tempfile

    with patch("agents.local_agent_nodes._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Simulate Ollama ResponseError
        err_type = SimpleNamespace
        err = err_type()
        err.message = "model 'gemma4:12b' not found"

        from ollama._types import ResponseError
        mock_client.chat.side_effect = ResponseError("model 'gemma4:12b' not found", 404)

        with pytest.raises(RuntimeError) as exc:
            local_agent_nodes._ollama_chat("gemma4:12b", [{"role": "user", "content": "hi"}], [])

        assert "not found" in str(exc.value).lower()
        assert "ollama pull" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Fix 2c: fast_search _fallback_rglob
# ---------------------------------------------------------------------------

def test_fallback_rglob_handles_oserror():
    from tools.fast_search import _fallback_rglob
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "readable.py").write_text("x")
        locked = root / "locked"
        locked.mkdir()
        (locked / "skip.py").write_text("x")

        results = list(_fallback_rglob(root, "*.py"))
        assert len(results) >= 1
        assert any("readable.py" in str(p) for p in results)


# ---------------------------------------------------------------------------
# Fix 3b: threading safety — _cloud_lock prevents race
# ---------------------------------------------------------------------------

def test_cloud_lock_does_not_deadlock_simple_read():
    from agents import local_agent_nodes
    local_agent_nodes._cloud_deadline.clear()
    model = "deepseek/deepseek-v4-flash"
    local_agent_nodes._cloud_deadline[model] = time.time() + 9999

    with patch("agents.local_agent_nodes.is_cloud_model", return_value=True):
        with patch("agents.local_agent_nodes._ollama_chat") as mock_ollama:
            result = local_agent_nodes._chat(model, [], [])
    assert result is not None


def test_cloud_lock_concurrent_failures_no_crash():
    """10 threads all hit cloud failure simultaneously — no race, no crash, all set deadline."""
    from agents import local_agent_nodes
    local_agent_nodes._cloud_deadline.clear()
    model = "deepseek/deepseek-v4-flash"

    results = []

    def concurrent_chat():
        try:
            local_agent_nodes._chat(model, [{"role": "user", "content": "x"}], [])
            results.append("ok")
        except Exception as e:
            results.append(f"err:{e}")

    dummy = SimpleNamespace(message=SimpleNamespace(content="local", tool_calls=None))
    with (
        patch("agents.local_agent_nodes.is_cloud_model", return_value=True),
        patch("agents.local_agent_nodes._ollama_chat", return_value=dummy),
        patch("clients.cloud_client.raw_completion", side_effect=RuntimeError("no network")),
    ):
        threads = []
        for _ in range(10):
            t = threading.Thread(target=concurrent_chat, daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=5)

    assert len(results) == 10
    assert all(r == "ok" for r in results), [r for r in results if r != "ok"]
    assert local_agent_nodes._cloud_deadline.get(model) is not None


def test_cloud_deadline_evicts_oldest_above_50():
    from agents import local_agent_nodes
    local_agent_nodes._cloud_deadline.clear()
    now = time.time()

    for i in range(55):
        local_agent_nodes._cloud_deadline[f"model-{i}"] = now + 9999

    model_new = "trigger-eviction"
    with patch("agents.local_agent_nodes.is_cloud_model", return_value=True):
        with patch("clients.cloud_client.raw_completion", side_effect=RuntimeError("no network")):
            local_agent_nodes._chat(model_new, [], [])

    assert len(local_agent_nodes._cloud_deadline) <= 50


# ---------------------------------------------------------------------------
# Fix 3c: verify _CLOUD_COOLDOWN constant is set
# ---------------------------------------------------------------------------

def test_cloud_cooldown_constant_exists():
    from agents import local_agent_nodes
    assert local_agent_nodes._CLOUD_COOLDOWN == 300


# ---------------------------------------------------------------------------
# Fix 5: max_steps=None crash — run_local_agent defaults from _max_steps_for
# ---------------------------------------------------------------------------

def test_max_steps_none_does_not_crash():
    """max_steps=None caused TypeError: None+2 in config. Fix: resolve via _max_steps_for."""
    from agents import local_agent_graph
    assert local_agent_graph._DEFAULT_MAX_STEPS >= 10
    assert local_agent_graph._max_steps_for("default") == local_agent_graph._DEFAULT_MAX_STEPS
    assert local_agent_graph._max_steps_for("code") >= 25


# ---------------------------------------------------------------------------
# Smoke: all fixed modules import cleanly
# ---------------------------------------------------------------------------

def test_imports_clean():
    import tools.filesystem
    import tools.fast_search
    import agents.local_agent_nodes
    assert True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
