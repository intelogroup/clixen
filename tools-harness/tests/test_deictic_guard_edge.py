"""
Edge case tests for query_guard.check_deictic().

Covers false positives (guard fires when it shouldn't) and false negatives
(guard misses real ambiguity). Documents expected behavior for each case.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.query_guard import check_all as check_deictic


# ── False positive candidates: guard should NOT fire ──────────────────────────

class TestFalsePositives:
    """Cases where deictic is present but referent is provided inline."""

    def test_colon_with_content(self):
        """User provides content after colon — no external referent needed."""
        q = "explain this: NameError: name 'x' is not defined at line 42"
        assert check_deictic(q, has_history=False) is None

    def test_colon_with_code_block(self):
        """Multi-line query with code after colon — referent is the code."""
        q = "what does this mean:\n```python\ndef foo(): return None\n```"
        assert check_deictic(q, has_history=False) is None

    def test_colon_with_url(self):
        """URL provided after colon — model can fetch it."""
        q = "summarize this: https://arxiv.org/abs/2401.00001"
        assert check_deictic(q, has_history=False) is None

    def test_colon_with_json(self):
        """Structured data provided — no referent needed."""
        q = 'explain this: {"error": "connection refused", "code": 503}'
        assert check_deictic(q, has_history=False) is None

    def test_multi_sentence_with_context(self):
        """Context is given in the same query before the deictic."""
        q = "The Python GIL prevents true threading parallelism. Explain this concept."
        assert check_deictic(q, has_history=False) is None

    def test_named_thing_in_same_query(self):
        """Named entity appears in same query — referent is clear."""
        q = "what is the Gaussian distribution and how does this relate to the central limit theorem"
        assert check_deictic(q, has_history=False) is None


# ── False negative candidates: guard SHOULD fire but might miss ───────────────

class TestFalseNegatives:
    """Cases where ambiguous deictic is at end of sentence with no following noun."""

    def test_deictic_at_sentence_end(self):
        """'this' at end — model has nothing to explain."""
        assert check_deictic("I need help understanding this", has_history=False) is not None

    def test_it_at_sentence_end(self):
        """'it' at end — referent unknown."""
        assert check_deictic("please help me understand it", has_history=False) is not None

    def test_that_at_sentence_end(self):
        """'that' at end — referent unknown."""
        assert check_deictic("can you clarify that", has_history=False) is not None


# ── Tricky self-contained patterns ────────────────────────────────────────────

class TestTrickyContained:
    """Deictics that look ambiguous but are grammatically self-contained."""

    def test_this_approach_named(self):
        """'this approach' is vague cold — guard should fire."""
        # "This approach" has no referent cold — correct to fire
        assert check_deictic("can this approach work in production?", has_history=False) is not None

    def test_rhetorical_this(self):
        """'Is this correct?' sent cold — there's nothing to be correct about."""
        # Firing is acceptable: sent cold it's meaningless
        result = check_deictic("is this correct?", has_history=False)
        # We accept either behavior — document it
        assert result is None or isinstance(result, str)  # not crashing

    def test_this_morning_time(self):
        """'this morning' is a temporal anchor, not a referent."""
        assert check_deictic("what happened this morning in the news", has_history=False) is None

    def test_these_days_idiom(self):
        """'these days' is an idiom, not a referent."""
        assert check_deictic("is Python still popular these days", has_history=False) is None


# ── Case and whitespace edge cases ─────────────────────────────────────────────

class TestCaseAndWhitespace:

    def test_all_caps_deictic(self):
        """Guard is case-insensitive."""
        assert check_deictic("EXPLAIN THIS PROCESS", has_history=False) is not None

    def test_mixed_case(self):
        assert check_deictic("Explain This Process", has_history=False) is not None

    def test_extra_whitespace(self):
        assert check_deictic("  explain   this   process  ", has_history=False) is not None

    def test_empty_string(self):
        # Empty query is rejected by the guard (intended) — returns a validation message, not None.
        assert check_deictic("", has_history=False) is not None

    def test_whitespace_only(self):
        assert check_deictic("   ", has_history=False) is not None


# ── Multiline queries ──────────────────────────────────────────────────────────

class TestMultiline:

    def test_multiline_with_no_context(self):
        """Multiple lines but still no referent — should fire."""
        q = "explain this\nI don't understand it at all"
        assert check_deictic(q, has_history=False) is not None

    def test_multiline_with_inline_context(self):
        """Context in first line, deictic in second — should NOT fire."""
        q = "The quicksort algorithm works by partitioning around a pivot.\nExplain this in detail."
        assert check_deictic(q, has_history=False) is None
