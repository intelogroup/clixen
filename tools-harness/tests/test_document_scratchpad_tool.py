def test_document_scratchpad_tool_writes_and_reads_visible_notes(tmp_path):
    from tools.document_scratchpad import execute

    written = execute("chat-1", str(tmp_path), "The scanned source has 14 pages.")
    read = execute("chat-1", str(tmp_path))

    assert "Scratchpad updated" in written
    assert "14 pages" in read


def test_document_task_includes_scratchpad_but_full_task_does_not():
    from agents.local_agent_tools import get_local_agent_tools

    document_names = {tool["function"]["name"] for tool in get_local_agent_tools("document")}
    full_names = {tool["function"]["name"] for tool in get_local_agent_tools("full")}

    assert "document_scratchpad" in document_names
    assert "document_scratchpad" not in full_names
