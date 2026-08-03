"""
Regression tests from a 10-query Telegram-simulated routing/action sweep covering local
filesystem actions, file creation, move/rename, and web search. Each test pins a bug found
by actually running the query through classify_telegram/classify + harness.run and checking
the real routing decision or filesystem outcome.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

import harness as m
import _harness_fs_actions as fsa
from clients.router import classify, classify_telegram


def test_path_after_preposition_resolves_multiword_subfolder():
    # "in my Downloads telegram_qa_sandbox folder" used to capture only "my".
    result = fsa._path_after_preposition(
        "list files in my Downloads telegram_qa_sandbox folder", ("in",)
    )
    assert result == str(Path.home() / "Downloads" / "telegram_qa_sandbox")


def test_create_file_with_content_is_not_treated_as_mkdir():
    # "create a text file ... folder" used to match the mkdir gate (folder keyword present
    # anywhere) and create a bogus directory instead of deferring to the write agent.
    action = m.parse_local_fs_action(
        "Create a text file called notes.txt in my Downloads folder with the content: hi"
    )
    assert action is None or action.tool_name != "create_directory"


def test_split_two_paths_combines_trailing_location_into_both_sides():
    # "rename X to Y in LOCATION" used to leave src unresolved (relative to cwd) and dump
    # the whole location clause into dest as a literal filename.
    result = fsa._split_two_paths(
        "rename a.txt to b.txt in my Downloads telegram_qa_sandbox folder", r"rename|move|mv"
    )
    home = str(Path.home() / "Downloads" / "telegram_qa_sandbox")
    assert result == (f"{home}/a.txt", f"{home}/b.txt")


def test_classify_telegram_filesystem_beats_bare_temporal_word():
    # "right now" used to hijack an unambiguous local-filesystem question into web search.
    _, intent = classify_telegram(
        "How many files are in my Downloads telegram_qa_sandbox folder right now?"
    )
    assert intent == "filesystem"


def test_classify_library_docs_guarded_by_temporal():
    # Bare mention of a product name ("OpenAI") used to steal current-events questions
    # into an ungrounded chat answer instead of grounded web search.
    _, intent = classify("Who is the current CEO of OpenAI?")
    assert intent != "library_docs"


def test_classify_telegram_library_docs_guarded_by_temporal():
    _, intent = classify_telegram("Who is the current CEO of OpenAI?")
    assert intent != "library_docs"


def _fake_tool_response(name: str, arguments: dict) -> dict:
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}}


def test_write_specialist_drops_stale_src_after_rename():
    import agents.specialists.write_specialist as ws
    import tools.registry as registry

    src = "/tmp/qa_sandbox/draft.txt"
    dest = "/tmp/qa_sandbox/archive/draft.txt"
    responses = [
        _fake_tool_response("write_file", {"path": src, "content": "hello world"}),
        _fake_tool_response("rename_file", {"src": src, "dest": dest}),
        {"message": {"content": "done", "tool_calls": []}},
    ]
    fake_executors = {"write_file": lambda a: "ok", "rename_file": lambda a: "ok"}
    with patch.object(ws, "ollama") as mock_ollama, \
         patch.dict(registry.EXECUTORS, fake_executors):
        mock_ollama.chat.side_effect = responses
        result = ws.run_write_specialist("create draft.txt then move it into archive")
    assert result.paths == [dest]
