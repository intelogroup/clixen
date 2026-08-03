"""
Regression and edge-case tests (Round 7) covering complex multi-turn/multi-tool Orchestrator
scenarios, document format resolution, code fence scrubbing, format-noise filtering, and
end-to-end doc agent simulation.
"""
import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

import telegram_bot as tb
from store.conversation import trim_to_budget


def test_telegram_doc_format_resolution():
    # Priority checks for extension detection
    assert tb._doc_format("save today's results in csv format") == ("Excel spreadsheet", ".xlsx")
    assert tb._doc_format("export to an Excel spreadsheet") == ("Excel spreadsheet", ".xlsx")
    assert tb._doc_format("make a word document from my notes") == ("Word document", ".docx")
    assert tb._doc_format("create slides representing the roadmap") == ("presentation", ".pptx")
    assert tb._doc_format("convert this text to standard output format") == ("PDF", ".pdf")


def test_strip_fences():
    # Strip fences with language prefixes
    assert tb._strip_fences("```markdown\n# Hello World\n```") == "# Hello World"
    assert tb._strip_fences("```csv\nname,age\nAlice,30\n```") == "name,age\nAlice,30"
    # String without fences should remain unaffected
    assert tb._strip_fences("Plain text content") == "Plain text content"
    # Improperly formed fences or empty content
    assert tb._strip_fences("```\n\n```") == ""


def test_content_query_format_noise_filtering():
    # Strip format noise words to ensure search queries target the content, not templates
    assert tb._content_query("today's matches as an excel sheet") == "today's matches as an"
    assert tb._content_query("convert notes to pdf format") == "convert notes to format"
    assert tb._content_query("space slides and presentation") == "space and"


@patch("telegram_bot._live_evidence")
@patch("clients.ollama_client.chat")
def test_run_doc_agent_synthesis_flow(mock_chat, mock_evidence, monkeypatch):
    mock_evidence.return_value = "- Match 1: France 3, Sweden 0"
    mock_chat.return_value = "```markdown\n# Match Results\nFrance beat Sweden 3-0.\n```"

    # Setup temporary directories and files
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock Path.home() so Downloads goes inside our temp dir
        monkeypatch.setattr("pathlib.Path.home", lambda: tb.Path(tmpdir))
        # Ensure Downloads folder exists
        os.makedirs(os.path.join(tmpdir, "Downloads"), exist_ok=True)

        # Mock the document conversion tool calls to prevent subprocess failures
        mock_pdf = MagicMock(return_value=os.path.join(tmpdir, "Downloads", "clixen_mock.pdf"))
        # Touch mock file so os.path.exists/size pass
        def fake_create_pdf(body, out):
            with open(out, "w") as f:
                f.write(body)
            return out

        monkeypatch.setattr("tools.document_create.create_pdf", fake_create_pdf)
        monkeypatch.setattr("store.conversation.get", lambda cid: [])

        # Trigger temporal query (forces live evidence gathering)
        msg, doc_path = tb._run_doc_agent(
            query="generate a pdf of today's match scores",
            model="gemma4:12b-mlx",
            chat_id="chat123",
            on_token=None,
        )

        assert "Here's your PDF." in msg
        assert doc_path is not None
        assert os.path.exists(doc_path)
        with open(doc_path, "r") as f:
            content = f.read()
            assert "# Match Results" in content
            assert "France beat Sweden 3-0." in content


@patch("clients.ollama_client.chat")
def test_run_doc_agent_empty_synthesis_fallback(mock_chat, monkeypatch):
    # LLM returns nothing
    mock_chat.return_value = ""
    monkeypatch.setattr("store.conversation.get", lambda cid: [])

    msg, doc_path = tb._run_doc_agent(
        query="make a word doc of my profile",
        model="gemma4:12b-mlx",
        chat_id="chat123",
        on_token=None,
    )

    assert "couldn't find the data to build that" in msg
    assert doc_path is None


def test_trim_to_budget_with_low_token_limits(monkeypatch):
    # Simulate a history that exceeds the model context window budget
    history = [
        {"role": "user", "content": "hello " * 200},
        {"role": "assistant", "content": "hi " * 200},
        {"role": "user", "content": "query " * 100},
    ]

    # Mock available_history_tokens to return a small budget (250 tokens)
    # The last user turn requires 150 tokens. With a total budget of 250 tokens,
    # the last turn fits, but the second one (150 tokens) does not, forcing trimming.
    monkeypatch.setattr("store.conversation.available_history_tokens", lambda m: 250)
    
    trimmed = trim_to_budget(history, "gemma4:12b-mlx", "current query")
    
    # Newer messages should be preserved over older ones
    assert len(trimmed) < len(history)
    # The last user turn should be preserved
    assert trimmed[-1]["role"] == "user"
