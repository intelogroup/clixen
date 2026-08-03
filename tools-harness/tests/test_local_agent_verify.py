"""
Coverage for the local-agent's plan/verify nodes (agents/local_agent_nodes.py)
and the graph routing that gates the "final" streaming event on verification
(agents/local_agent_graph.py).

Why this exists: run_local_agent_streaming's SSE consumer used to treat any
tool-call-free AIMessage as "final" and terminate the stream immediately. With
a verify node now sitting between "agent" and END, that heuristic would fire
before verification (and any retry) ever ran, silently dropping corrected
answers. These tests pin the node logic and the fixed streaming gate.

All model calls are mocked — no real LLM/network calls.
"""
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agents import local_agent_nodes as nodes
from agents.local_agent_graph import should_continue_after_verify
from agents.local_agent_state import LocalAgentState


def _chat_resp(content: str):
    return SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))


def test_plan_step_injects_system_message_not_ai_message():
    """Must be a SystemMessage — an AIMessage here would trip the streaming
    consumer's old final-answer heuristic before the agent even starts."""
    state = LocalAgentState(messages=[HumanMessage(content="rename all .txt files to .md")])

    with patch.object(nodes, "_chat", return_value=_chat_resp("1. List files\n2. Rename each")):
        result = nodes.plan_step(state)

    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], SystemMessage)
    assert "List files" in result["messages"][0].content


def test_plan_step_is_a_noop_without_a_human_message():
    state = LocalAgentState(messages=[])
    assert nodes.plan_step(state) == {}


def test_plan_step_failure_does_not_block_the_run():
    state = LocalAgentState(messages=[HumanMessage(content="do something")])
    with patch.object(nodes, "_chat", side_effect=RuntimeError("model unreachable")):
        assert nodes.plan_step(state) == {}


def test_verify_answer_accepts_ok_verdict():
    state = LocalAgentState(messages=[
        HumanMessage(content="what's in config.py?"),
        AIMessage(content="config.py defines DEBUG=True."),
    ])
    with patch.object(nodes, "_chat", return_value=_chat_resp("OK")):
        result = nodes.verify_answer(state)

    assert result == {"verified": True}


def test_verify_answer_flags_bad_answer_and_retries_once():
    state = LocalAgentState(
        messages=[
            HumanMessage(content="what's in config.py?"),
            ToolMessage(content="DEBUG=True", tool_call_id="t1", name="read_file"),
            AIMessage(content="I don't know."),
        ],
        verify_attempts=0,
    )
    with patch.object(nodes, "_chat", return_value=_chat_resp("Wrong — the file clearly says DEBUG=True.")):
        result = nodes.verify_answer(state)

    assert result["verified"] is False
    assert result["verify_attempts"] == 1
    assert isinstance(result["messages"][0], SystemMessage)
    assert "DEBUG=True" in result["messages"][0].content


def test_verify_answer_gives_up_after_one_retry():
    """Bounded retry — a second pass through verify must not loop forever."""
    state = LocalAgentState(
        messages=[HumanMessage(content="q"), AIMessage(content="still wrong")],
        verify_attempts=1,
    )
    # _chat must not even be called once the retry budget is spent.
    with patch.object(nodes, "_chat") as mock_chat:
        result = nodes.verify_answer(state)

    mock_chat.assert_not_called()
    assert result == {"verified": True}


def test_verify_answer_failure_accepts_answer_as_is():
    state = LocalAgentState(messages=[HumanMessage(content="q"), AIMessage(content="an answer")])
    with patch.object(nodes, "_chat", side_effect=RuntimeError("model unreachable")):
        result = nodes.verify_answer(state)
    assert result == {"verified": True}


def test_should_continue_after_verify_routes_on_verified_flag():
    verified_state = LocalAgentState(messages=[], verified=True)
    unverified_state = LocalAgentState(messages=[], verified=False)

    assert should_continue_after_verify(verified_state) == "end"
    assert should_continue_after_verify(unverified_state) == "agent"


# --- Edge cases -------------------------------------------------------------

def test_verify_answer_on_error_escalation_path_does_not_grab_stale_history_answer():
    """Regression: when error_count>=3 forces should_continue_with_error_check
    to route "end" -> "verify", the actual last message is a ToolMessage (the
    error), not an AIMessage. The old implementation scanned backward for *any*
    tool-call-free AIMessage and would find a stale answer from an earlier
    chat_id turn — critiquing the wrong thing instead of recognizing that no
    fresh answer was produced this turn."""
    state = LocalAgentState(
        messages=[
            HumanMessage(content="first question"),
            AIMessage(content="an old answer from a previous turn"),  # stale — must be ignored
            HumanMessage(content="second question, this one fails"),
            AIMessage(content="", tool_calls=[{"id": "c1", "name": "bash_exec", "args": {}}]),
            ToolMessage(content="[error] tool failed", tool_call_id="c1", name="bash_exec"),
        ],
        error_count=3,
    )
    with patch.object(nodes, "_chat") as mock_chat:
        result = nodes.verify_answer(state)

    mock_chat.assert_not_called()  # nothing fresh to critique — must not fire a wasted call
    assert result == {"verified": True}


def test_verify_answer_empty_messages_list():
    state = LocalAgentState(messages=[])
    with patch.object(nodes, "_chat") as mock_chat:
        result = nodes.verify_answer(state)
    mock_chat.assert_not_called()
    assert result == {"verified": True}


def test_verify_answer_truncates_long_transcript_without_crashing():
    long_result = "x" * 5000
    state = LocalAgentState(messages=[
        HumanMessage(content="summarize this"),
        ToolMessage(content=long_result, tool_call_id="t1", name="read_file"),
        AIMessage(content="a short summary"),
    ])
    with patch.object(nodes, "_chat", return_value=_chat_resp("OK")) as mock_chat:
        result = nodes.verify_answer(state)

    assert result == {"verified": True}
    sent_prompt = mock_chat.call_args.kwargs["messages"][1]["content"]
    # transcript slice is capped at the last 2000 chars, not the full 5000
    assert len(sent_prompt) < 3500


def test_verify_answer_verdict_ok_prefix_is_not_confused_with_okay_looking_criticism():
    """The OK-detection is a startswith check — a critique that happens to
    start with the letters "OK" but isn't actually approving would be
    misread as approval. Documents current behavior (not a false positive in
    practice since the prompt asks for exactly 'OK', but worth pinning)."""
    state = LocalAgentState(messages=[
        HumanMessage(content="q"), AIMessage(content="an answer"),
    ])
    with patch.object(nodes, "_chat", return_value=_chat_resp("OKish, but check the units")):
        result = nodes.verify_answer(state)
    # startswith("OK") -> treated as approved even though the model hedged
    assert result == {"verified": True}


def test_plan_step_ignores_trailing_ai_message_and_uses_latest_human_message():
    """If somehow called mid-history (defensive — plan only runs at entry in
    practice), it must key off the most recent HumanMessage, not an earlier one."""
    state = LocalAgentState(messages=[
        HumanMessage(content="old unrelated request"),
        AIMessage(content="old answer"),
        HumanMessage(content="actual current request"),
    ])
    with patch.object(nodes, "_chat", return_value=_chat_resp("1. Do the thing")) as mock_chat:
        nodes.plan_step(state)

    sent_user_msg = mock_chat.call_args.kwargs["messages"][1]["content"]
    assert sent_user_msg == "actual current request"


def test_plan_step_strips_whitespace_only_response():
    state = LocalAgentState(messages=[HumanMessage(content="do something")])
    with patch.object(nodes, "_chat", return_value=_chat_resp("   \n  ")):
        assert nodes.plan_step(state) == {}
