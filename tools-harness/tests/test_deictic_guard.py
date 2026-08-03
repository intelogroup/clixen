"""
Unit tests for query_guard.check_deictic().

Covers:
  - fires on deictic references with no history
  - suppressed when history present
  - suppressed for self-contained deictics
  - suppressed for explicit, non-deictic queries
  - harness integration: guard short-circuits before model call
"""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.query_guard import check_all as check_deictic
from clients.router import Classification


# ── check_deictic unit tests ───────────────────────────────────────────────────

class TestDeicticFires:
    """Guard should return a clarifying question."""

    def test_explain_this_process(self):
        result = check_deictic("explain me in details this process", has_history=False)
        assert result is not None
        assert "?" in result

    def test_tell_me_about_it(self):
        assert check_deictic("tell me about it", has_history=False) is not None

    def test_how_does_that_work(self):
        assert check_deictic("how does that work", has_history=False) is not None

    def test_explain_this_algorithm(self):
        assert check_deictic("explain this sorting algorithm", has_history=False) is not None

    def test_describe_that_concept(self):
        assert check_deictic("describe that concept", has_history=False) is not None

    def test_what_is_it(self):
        assert check_deictic("what is it", has_history=False) is not None

    def test_summarize_it(self):
        assert check_deictic("summarize it", has_history=False) is not None

    def test_explain_this(self):
        assert check_deictic("explain this", has_history=False) is not None


class TestDeicticSuppressedWithHistory:
    """When conversation history exists, guard must not fire — referent is known."""

    def test_explain_this_process_with_history(self):
        assert check_deictic("explain this process", has_history=True) is None

    def test_tell_me_about_it_with_history(self):
        assert check_deictic("tell me about it", has_history=True) is None

    def test_how_does_that_work_with_history(self):
        assert check_deictic("how does that work", has_history=True) is None


class TestDeicticSuppressedSelfContained:
    """Self-contained deictics that don't imply an external referent."""

    def test_this_year(self):
        assert check_deictic("what happened this year", has_history=False) is None

    def test_this_week(self):
        assert check_deictic("news from this week", has_history=False) is None

    def test_that_said(self):
        assert check_deictic("that said, what is Python?", has_history=False) is None

    def test_it_depends(self):
        assert check_deictic("it depends on the context", has_history=False) is None

    def test_it_is(self):
        assert check_deictic("it is a good day", has_history=False) is None

    def test_this_is(self):
        assert check_deictic("this is a test query", has_history=False) is None


class TestDeicticSuppressedExplicit:
    """Queries with no deictic reference — should always pass through."""

    def test_explicit_concept(self):
        assert check_deictic("what is the agency problem in economics", has_history=False) is None

    def test_explicit_algorithm(self):
        assert check_deictic("explain quicksort algorithm", has_history=False) is None

    def test_temporal_query(self):
        # 2026-07-11: was "what is the weather today" — stale since the
        # query_guard weather-no-location guard (query_guard.py:~405, added
        # 2026-07-10) legitimately intercepts that exact query before this
        # deictic check even matters, unrelated to deictic-reference
        # detection. Swapped to a temporal query no other guard fires on.
        assert check_deictic("what is today's date", has_history=False) is None

    def test_named_person(self):
        assert check_deictic("who is Alan Turing", has_history=False) is None

    def test_code_question(self):
        assert check_deictic("how do I reverse a list in Python", has_history=False) is None


# ── harness integration: guard short-circuits before local_chat ────────────────

class TestHarnessDeicticGuard:
    """Guard in harness.py must return clarifying question without calling the model."""

    def test_cold_deictic_skips_model(self):
        """harness.run() with empty history + deictic query must NOT call local_chat."""
        import harness
        with (
            patch.object(harness, "local_chat") as mock_chat,
            patch.object(harness, "conv_get", return_value=[]),
            patch.object(harness, "conv_append"),
            patch.object(harness, "trim_to_budget", return_value=[]),
            patch.object(harness, "compact_old_turns"),
            patch.object(harness, "get_lock", return_value=MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))),
        ):
            response, model_used, intent = harness.run(
                "explain me in details this process",
                chat_id="test-session-1",
            )

        mock_chat.assert_not_called()
        assert "?" in response
        assert model_used == "guard"
        assert intent == "unclear"

    def test_cold_explicit_query_reaches_model(self):
        """Explicit query with no deictic must reach local_chat normally."""
        import harness
        mock_result = "The agency problem is a conflict of interest..."
        with (
            # Pin a plain (non-grounded) intent so the test isolates guard pass-through,
            # not the classifier — "what is..." now classifies as factual_qa (search-grounded).
            # 2026-07-11: run()'s orchestrated path now calls classify_message() (LLM-primary)
            # instead of the bare regex classify() — patch that instead so this stays
            # hermetic (no real cloud call).
            patch.object(
                harness, "classify_message",
                return_value=Classification(model="gemma4:12b-mlx", intent="analysis", specialist_hint=None, source="test"),
            ),
            patch.object(harness, "local_chat", return_value=mock_result) as mock_chat,
            patch.object(harness, "conv_get", return_value=[]),
            patch.object(harness, "conv_append"),
            patch.object(harness, "trim_to_budget", return_value=[]),
            patch.object(harness, "compact_old_turns"),
            patch.object(harness, "get_lock", return_value=MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))),
        ):
            response, model_used, intent = harness.run(
                "what is the agency problem in economics",
                chat_id="test-session-2",
            )

        mock_chat.assert_called_once()
        assert model_used != "guard"
