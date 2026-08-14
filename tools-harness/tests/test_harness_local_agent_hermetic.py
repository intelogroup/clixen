"""Hermetic entry-point smoke test for local filesystem routing."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_harness_routes_scoped_filesystem_work_without_model_transport(tmp_path):
    import harness
    from store import conversation
    from tools.registry import execute_tool

    note = tmp_path / "note.txt"
    note.write_text("local evidence")
    chat_id = "hermetic-local-agent"
    conversation.clear(chat_id)
    observed = {}

    def fake_local_agent(**kwargs):
        observed.update(kwargs)
        result = execute_tool("read_file", {"path": "note.txt"})
        return str(result)

    with patch.object(harness, "run_local_agent", side_effect=fake_local_agent), \
         patch.object(harness.ollama_client, "chat", side_effect=AssertionError("Ollama must not run")):
        result, route, intent = harness.run(
            "read note.txt",
            model="gemma4:12b",
            chat_id=chat_id,
            project_root=str(tmp_path),
            force_local_agent=True,
            orchestrated=False,
            intent="filesystem",
        )

    assert "local evidence" in result
    assert route == "local-agent-graph"
    assert intent == "filesystem"
    assert observed["project_root"] == str(tmp_path)
    assert any(turn["content"] == result for turn in conversation.get(chat_id))
    conversation.clear(chat_id)
