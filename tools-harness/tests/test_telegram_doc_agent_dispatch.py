"""
Regression tests for telegram_bot.py's _run_doc_agent cloud-dispatch fix.

_run_doc_agent used to import clients.ollama_client.chat directly (a
local-only call, no cloud fallback) and call it with cls.model — the
cloud-first router's model choice, usually a cloud string like
"deepseek/deepseek-v4-flash". That 404s against local Ollama since no local
model has that name. Confirmed live in telegram_bot.log (repeating every few
minutes, 2026-07-14) until fixed by swapping the import for
harness.local_chat (the existing is_cloud_model()-dispatching wrapper).

telegram_bot.py itself loads a real faster-whisper model at import time
(_whisper = _WhisperModel(...) at module scope) — too heavy to import in a
test, so this checks the fix two ways: (1) source inspection to guard against
the bad import being reintroduced, (2) a functional test of harness.local_chat
itself (what _run_doc_agent now calls) proving it never reaches the local
Ollama client for a cloud model string.
"""
import re
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

_TELEGRAM_BOT_SRC = (Path(__file__).parent.parent / "telegram_bot.py").read_text()


def _function_body(name: str) -> str:
    m = re.search(rf"^def {re.escape(name)}\(.*?\n(?=^def |\Z)", _TELEGRAM_BOT_SRC, re.S | re.M)
    assert m, f"could not locate function {name!r} in telegram_bot.py"
    return m.group(0)


def _non_comment_lines(body: str) -> str:
    return "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))


def test_run_doc_agent_does_not_import_ollama_client_directly():
    body = _non_comment_lines(_function_body("_run_doc_agent"))
    assert "clients.ollama_client" not in body, (
        "_run_doc_agent re-imports clients.ollama_client directly — this bypasses "
        "cloud dispatch and will 404 on a cloud-routed model string (regression)"
    )


def test_run_doc_agent_uses_harness_local_chat():
    body = _function_body("_run_doc_agent")
    assert "from harness import local_chat as llm_chat" in body


def test_local_chat_dispatches_cloud_model_to_cloud_client_only():
    import harness

    with patch.object(harness.cloud_client, "chat", return_value="ok") as mock_cloud, \
         patch.object(harness.ollama_client, "chat") as mock_local:
        result = harness.local_chat(user_message="hi", model="deepseek/deepseek-v4-flash", tools=[])

    assert result == "ok"
    mock_cloud.assert_called_once()
    mock_local.assert_not_called()


def test_local_chat_dispatches_local_model_to_ollama_only():
    import harness

    with patch.object(harness.cloud_client, "chat") as mock_cloud, \
         patch.object(harness.ollama_client, "chat", return_value="ok") as mock_local:
        result = harness.local_chat(user_message="hi", model="gemma4:12b-mlx", tools=[])

    assert result == "ok"
    mock_local.assert_called_once()
    mock_cloud.assert_not_called()


if __name__ == "__main__":
    test_run_doc_agent_does_not_import_ollama_client_directly()
    test_run_doc_agent_uses_harness_local_chat()
    test_local_chat_dispatches_cloud_model_to_cloud_client_only()
    test_local_chat_dispatches_local_model_to_ollama_only()
    print("telegram doc-agent dispatch tests passed")
